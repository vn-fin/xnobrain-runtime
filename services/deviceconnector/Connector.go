// <Summary>
// Connector maintains one outbound connection loop, rotates credentials,
// validates and journals commands, retries acknowledgements, and never blocks
// local operation when the cloud is offline.
// File: Connector.go
// Types: Connector, Config, Status
// </Summary>
package deviceconnector

import (
	"context"
	"errors"
	"fmt"
	"math/rand/v2"
	"path/filepath"
	"sync"
	"time"

	"github.com/rs/zerolog/log"
	"github.com/xno/open-lumora/internal/device"
	"github.com/xno/open-lumora/internal/studio/contract"
)

var (
	ErrRevoked  = errors.New("device registration revoked")
	errUnpaired = errors.New("device is locally unpaired")
)

type Config struct {
	DataDir                string
	Endpoint               string
	ServerSigningPublicKey string
	PollInterval           time.Duration
	BackoffMinimum         time.Duration
	BackoffMaximum         time.Duration
}

type Status struct {
	Enabled         bool       `json:"enabled"`
	Connected       bool       `json:"connected"`
	DeviceID        string     `json:"device_id,omitempty"`
	Endpoint        string     `json:"endpoint,omitempty"`
	LastConnectedAt *time.Time `json:"last_connected_at,omitempty"`
	LastErrorCode   string     `json:"last_error_code,omitempty"`
	QueuedCommands  int        `json:"queued_commands"`
	RecoveryCode    string     `json:"recovery_code,omitempty"`
	Claimed         bool       `json:"claimed"`
}

type CommandDispatcher interface {
	ExecuteManagedCron(ctx context.Context, agentID string, cronID string, commandID string) error
}

type NotificationSink interface {
	StoreNotifications(ctx context.Context, notifications []contract.Notification) error
}

type Connector struct {
	config        Config
	controlPlane  ControlPlane
	dispatcher    CommandDispatcher
	notifications NotificationSink
	identity      *device.Identity
	store         *device.Store
	journal       *device.Journal
	mu            sync.RWMutex
	status        Status
	cancel        context.CancelFunc
	closeOnce     sync.Once
	wake          chan struct{}
	now           func() time.Time
}

func NewConnector(config Config, controlPlane ControlPlane, dispatcher CommandDispatcher, notifications NotificationSink) (*Connector, error) {
	if controlPlane == nil || dispatcher == nil {
		return nil, fmt.Errorf("device control plane and dispatcher are required")
	}
	if config.PollInterval <= 0 {
		config.PollInterval = 20 * time.Second
	}
	if config.BackoffMinimum <= 0 {
		config.BackoffMinimum = time.Second
	}
	if config.BackoffMaximum < config.BackoffMinimum {
		config.BackoffMaximum = time.Minute
	}
	root := filepath.Join(config.DataDir, "device")
	identity, err := device.LoadOrCreateIdentity(root)
	if err != nil {
		return nil, err
	}
	store, err := device.NewStore(root)
	if err != nil {
		return nil, err
	}
	journal, err := device.NewJournal(filepath.Join(root, "journal"))
	if err != nil {
		return nil, err
	}
	enabled, err := store.Enabled()
	if err != nil {
		return nil, err
	}
	return &Connector{config: config, controlPlane: controlPlane, dispatcher: dispatcher, notifications: notifications, identity: identity, store: store, journal: journal, status: Status{Enabled: enabled, Endpoint: config.Endpoint}, wake: make(chan struct{}, 1), now: func() time.Time { return time.Now().UTC() }}, nil
}

func (c *Connector) Start(ctx context.Context) error {
	runContext, cancel := context.WithCancel(ctx)
	c.mu.Lock()
	c.cancel = cancel
	c.mu.Unlock()
	backoff := c.config.BackoffMinimum
	for {
		if err := runContext.Err(); err != nil {
			return nil
		}
		err := c.cycle(runContext)
		if errors.Is(err, errUnpaired) {
			select {
			case <-runContext.Done():
				return nil
			case <-c.wake:
				continue
			}
		}
		if errors.Is(err, ErrRevoked) {
			c.setDisconnected("revoked")
			return err
		}
		if err == nil {
			backoff = c.config.BackoffMinimum
			continue
		} else {
			c.setDisconnected(errorCode(err))
			log.Warn().Str("error_code", errorCode(err)).Msg("device connector retrying")
		}
		jitter := time.Duration(rand.Int64N(max(int64(backoff/3), 1)))
		select {
		case <-runContext.Done():
			return nil
		case <-c.wake:
		case <-time.After(backoff + jitter):
		}
		backoff *= 2
		if backoff > c.config.BackoffMaximum {
			backoff = c.config.BackoffMaximum
		}
	}
}

func (c *Connector) Close() error {
	c.closeOnce.Do(func() {
		c.mu.Lock()
		if c.cancel != nil {
			c.cancel()
		}
		c.status.Connected = false
		c.mu.Unlock()
	})
	return nil
}

func (c *Connector) Status() Status {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.status
}

func (c *Connector) Unpair(_ context.Context) error {
	if err := c.store.Clear(); err != nil {
		return err
	}
	if err := c.store.SetEnabled(false); err != nil {
		return err
	}
	c.mu.Lock()
	c.status.Enabled = false
	c.status.DeviceID = ""
	c.status.Connected = false
	c.status.RecoveryCode = ""
	c.status.Claimed = false
	c.mu.Unlock()
	select {
	case c.wake <- struct{}{}:
	default:
	}
	return nil
}

func (c *Connector) Pair(_ context.Context) error {
	if err := c.store.SetEnabled(true); err != nil {
		return err
	}
	c.mu.Lock()
	c.status.Enabled = true
	c.status.LastErrorCode = ""
	c.mu.Unlock()
	select {
	case c.wake <- struct{}{}:
	default:
	}
	return nil
}

func (c *Connector) cycle(ctx context.Context) error {
	c.mu.RLock()
	enabled := c.status.Enabled
	c.mu.RUnlock()
	if !enabled {
		return errUnpaired
	}
	registration, err := c.registration(ctx)
	if err != nil {
		return err
	}
	validator, err := device.NewValidator(registration.DeviceID, c.config.ServerSigningPublicKey)
	if err != nil {
		return err
	}
	response, err := c.controlPlane.Poll(ctx, registration.AccessToken, PollRequest{DeviceID: registration.DeviceID, Cursor: registration.Cursor})
	if err != nil {
		return err
	}
	if response.Revoked {
		_ = c.store.Clear()
		_ = c.store.SetEnabled(false)
		c.mu.Lock()
		c.status.Enabled = false
		c.mu.Unlock()
		return ErrRevoked
	}
	if registration.Claimed != response.Claimed {
		registration.Claimed = response.Claimed
		if err := c.store.Save(registration); err != nil {
			return err
		}
	}
	now := c.now()
	c.mu.Lock()
	c.status.Connected = true
	c.status.DeviceID = registration.DeviceID
	c.status.LastConnectedAt = &now
	c.status.LastErrorCode = ""
	c.status.QueuedCommands = len(response.Commands)
	c.status.RecoveryCode = registration.RecoveryCode
	c.status.Claimed = registration.Claimed
	c.mu.Unlock()
	if c.notifications != nil && len(response.Notifications) > 0 {
		if err := c.notifications.StoreNotifications(ctx, response.Notifications); err != nil {
			return err
		}
	}
	for _, command := range response.Commands {
		if err := c.process(ctx, validator, registration.AccessToken, command); err != nil {
			return err
		}
		c.mu.Lock()
		c.status.QueuedCommands--
		c.mu.Unlock()
	}
	registration.Cursor = response.Cursor
	if err := c.store.Save(registration); err != nil {
		return err
	}
	select {
	case <-ctx.Done():
		return nil
	case <-c.wake:
		return nil
	case <-time.After(c.config.PollInterval):
		return nil
	}
}

func (c *Connector) registration(ctx context.Context) (device.Registration, error) {
	registration, exists, err := c.store.Load()
	if err != nil {
		return device.Registration{}, err
	}
	if !exists {
		challenge, err := c.controlPlane.Challenge(ctx, c.identity.PublicKey())
		if err != nil {
			return device.Registration{}, err
		}
		registration, err = c.controlPlane.Register(ctx, RegistrationRequest{PublicKey: c.identity.PublicKey(), Challenge: challenge.Challenge, Proof: c.identity.Sign([]byte(challenge.Challenge)), Version: "0.1.0"})
		if err != nil {
			return device.Registration{}, err
		}
		if registration.DeviceID == "" || registration.AccessToken == "" || registration.RefreshToken == "" {
			return device.Registration{}, fmt.Errorf("incomplete device registration")
		}
		if err := c.store.Save(registration); err != nil {
			return device.Registration{}, err
		}
	}
	if registration.ExpiresAt.Before(c.now().Add(time.Minute)) {
		refreshed, err := c.controlPlane.Refresh(ctx, RefreshRequest{DeviceID: registration.DeviceID, RefreshToken: registration.RefreshToken, Proof: c.identity.Sign([]byte(registration.DeviceID + "\n" + registration.RefreshToken))})
		if err != nil {
			return device.Registration{}, err
		}
		refreshed.Cursor = registration.Cursor
		refreshed.RecoveryCode = registration.RecoveryCode
		registration = refreshed
		if err := c.store.Save(registration); err != nil {
			return device.Registration{}, err
		}
	}
	return registration, nil
}

func (c *Connector) process(ctx context.Context, validator *device.Validator, token string, command device.Command) error {
	if receipt, exists, err := c.journal.Get(command.CommandID); err != nil {
		return err
	} else if exists && receipt.Status == "running" {
		interrupted, transitionErr := c.journal.Transition(command.CommandID, "failed", "execution_interrupted")
		if transitionErr != nil {
			return transitionErr
		}
		return c.controlPlane.Acknowledge(ctx, token, interrupted)
	} else if exists && isTerminalStatus(receipt.Status) {
		return c.controlPlane.Acknowledge(ctx, token, receipt)
	}
	if err := validator.Validate(command); err != nil {
		status := "rejected"
		if !command.ExpiresAt.After(c.now()) {
			status = "expired"
		}
		receipt, journalErr := c.journal.Transition(command.CommandID, status, errorCode(err))
		if journalErr != nil {
			return journalErr
		}
		return c.controlPlane.Acknowledge(ctx, token, receipt)
	}
	receipt, err := c.journal.Transition(command.CommandID, "accepted", "")
	if err != nil {
		return err
	}
	_ = c.controlPlane.Acknowledge(ctx, token, receipt)
	receipt, err = c.journal.Transition(command.CommandID, "running", "")
	if err != nil {
		return err
	}
	_ = c.controlPlane.Acknowledge(ctx, token, receipt)
	status, code := "completed", ""
	if err := c.dispatcher.ExecuteManagedCron(ctx, command.AgentID, command.ResourceID, command.CommandID); err != nil {
		status, code = "failed", errorCode(err)
	}
	receipt, err = c.journal.Transition(command.CommandID, status, code)
	if err != nil {
		return err
	}
	return c.controlPlane.Acknowledge(ctx, token, receipt)
}

func (c *Connector) setDisconnected(code string) {
	c.mu.Lock()
	c.status.Connected = false
	c.status.LastErrorCode = code
	c.mu.Unlock()
}

func errorCode(err error) string {
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return "context_unavailable"
	}
	if errors.Is(err, ErrRevoked) {
		return "device_revoked"
	}
	return "operation_failed"
}

func isTerminalStatus(status string) bool {
	return status == "completed" || status == "failed" || status == "expired" || status == "rejected" || status == "cancelled"
}

func max(left int64, right int64) int64 {
	if left > right {
		return left
	}
	return right
}

var _ contract.Connector = (*Connector)(nil)

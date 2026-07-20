// <Summary>
// Connector tests drive registration proof, token refresh, signed dispatch,
// duplicate delivery, acknowledgement retry state, revocation, and local unpair.
// File: Connector_test.go
// Tests: connector lifecycle against an in-memory control plane
// </Summary>
package deviceconnector

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/xno/open-lumora/internal/device"
)

type fakeControlPlane struct {
	mu           sync.Mutex
	registration device.Registration
	challenge    string
	register     RegistrationRequest
	commands     []device.Command
	revoked      bool
	refreshes    int
	polls        int
	receipts     []device.Receipt
	ackFailures  map[string]int
}

func (f *fakeControlPlane) Challenge(_ context.Context, _ string) (RegistrationChallenge, error) {
	return RegistrationChallenge{Challenge: f.challenge}, nil
}

func (f *fakeControlPlane) Register(_ context.Context, request RegistrationRequest) (device.Registration, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.register = request
	return f.registration, nil
}

func (f *fakeControlPlane) Refresh(_ context.Context, _ RefreshRequest) (device.Registration, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.refreshes++
	registration := f.registration
	registration.AccessToken = "rotated-access"
	registration.ExpiresAt = time.Now().Add(time.Hour)
	return registration, nil
}

func (f *fakeControlPlane) Poll(_ context.Context, _ string, _ PollRequest) (PollResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.polls++
	return PollResponse{Cursor: "cursor-1", Commands: append([]device.Command(nil), f.commands...), Revoked: f.revoked}, nil
}

func (f *fakeControlPlane) Acknowledge(_ context.Context, _ string, receipt device.Receipt) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.receipts = append(f.receipts, receipt)
	if f.ackFailures[receipt.Status] > 0 {
		f.ackFailures[receipt.Status]--
		return errors.New("network unavailable")
	}
	return nil
}

type countingDispatcher struct {
	mu    sync.Mutex
	calls int
	err   error
}

func (d *countingDispatcher) ExecuteManagedCron(_ context.Context, _, _, _ string) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.calls++
	return d.err
}

func TestConnectorRegistersDispatchesAndDeduplicates(t *testing.T) {
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	control := &fakeControlPlane{challenge: "proof-challenge", registration: device.Registration{DeviceID: "dev-1", AccessToken: "access", RefreshToken: "refresh", ExpiresAt: now.Add(time.Hour), RecoveryCode: "recover-once"}}
	control.commands = []device.Command{connectorCommand(private, now)}
	dispatcher := &countingDispatcher{}
	connector, err := NewConnector(Config{DataDir: t.TempDir(), Endpoint: "https://cloud.example", ServerSigningPublicKey: base64.RawStdEncoding.EncodeToString(public), PollInterval: time.Nanosecond}, control, dispatcher, nil)
	if err != nil {
		t.Fatal(err)
	}
	connector.now = func() time.Time { return now }
	if err := connector.cycle(context.Background()); err != nil {
		t.Fatal(err)
	}
	registeredPublic, _ := base64.RawStdEncoding.DecodeString(control.register.PublicKey)
	proof, _ := base64.RawStdEncoding.DecodeString(control.register.Proof)
	if !ed25519.Verify(registeredPublic, []byte(control.challenge), proof) {
		t.Fatal("registration did not prove device-key possession")
	}
	if dispatcher.calls != 1 {
		t.Fatalf("dispatch calls = %d", dispatcher.calls)
	}
	if len(control.receipts) != 3 || control.receipts[0].Status != "accepted" || control.receipts[1].Status != "running" || control.receipts[2].Status != "completed" {
		t.Fatalf("unexpected receipts: %+v", control.receipts)
	}
	if err := connector.cycle(context.Background()); err != nil {
		t.Fatal(err)
	}
	if dispatcher.calls != 1 {
		t.Fatalf("duplicate command executed %d times", dispatcher.calls)
	}
	if control.receipts[len(control.receipts)-1].Status != "completed" {
		t.Fatal("duplicate did not return the original terminal receipt")
	}
	status := connector.Status()
	if !status.Connected || status.DeviceID != "dev-1" || status.RecoveryCode != "recover-once" {
		t.Fatalf("unexpected connector status: %+v", status)
	}
}

func TestConnectorRejectsInvalidCommandRefreshesAndRevokes(t *testing.T) {
	public, private, _ := ed25519.GenerateKey(rand.Reader)
	now := time.Now().UTC()
	command := connectorCommand(private, now)
	command.Signature = base64.RawStdEncoding.EncodeToString(make([]byte, ed25519.SignatureSize))
	root := t.TempDir()
	control := &fakeControlPlane{challenge: "challenge", registration: device.Registration{DeviceID: "dev-1", AccessToken: "old", RefreshToken: "refresh", ExpiresAt: now.Add(10 * time.Second)}, commands: []device.Command{command}}
	dispatcher := &countingDispatcher{}
	connector, err := NewConnector(Config{DataDir: root, Endpoint: "https://cloud.example", ServerSigningPublicKey: base64.RawStdEncoding.EncodeToString(public), PollInterval: time.Nanosecond}, control, dispatcher, nil)
	if err != nil {
		t.Fatal(err)
	}
	connector.now = func() time.Time { return now }
	if err := connector.cycle(context.Background()); err != nil {
		t.Fatal(err)
	}
	if control.refreshes != 1 || dispatcher.calls != 0 || control.receipts[len(control.receipts)-1].Status != "rejected" {
		t.Fatalf("invalid command handling: refreshes=%d calls=%d receipts=%+v", control.refreshes, dispatcher.calls, control.receipts)
	}
	control.revoked = true
	control.commands = nil
	if err := connector.cycle(context.Background()); !errors.Is(err, ErrRevoked) {
		t.Fatalf("expected revocation, got %v", err)
	}
	if _, err := os.Stat(filepath.Join(root, "device", "registration.json")); !os.IsNotExist(err) {
		t.Fatalf("revocation retained cloud credentials: %v", err)
	}
	if _, err := os.Stat(filepath.Join(root, "device", "identity.key")); err != nil {
		t.Fatalf("revocation erased local identity unexpectedly: %v", err)
	}
}

func TestConnectorStatusNetworkLossDoesNotDuplicateOrBlockExecution(t *testing.T) {
	public, private, _ := ed25519.GenerateKey(rand.Reader)
	now := time.Now().UTC()
	control := &fakeControlPlane{challenge: "challenge", registration: device.Registration{DeviceID: "dev-1", AccessToken: "access", RefreshToken: "refresh", ExpiresAt: now.Add(time.Hour)}, commands: []device.Command{connectorCommand(private, now)}, ackFailures: map[string]int{"accepted": 1, "running": 1, "completed": 1}}
	dispatcher := &countingDispatcher{}
	connector, err := NewConnector(Config{DataDir: t.TempDir(), Endpoint: "https://cloud.example", ServerSigningPublicKey: base64.RawStdEncoding.EncodeToString(public), PollInterval: time.Nanosecond}, control, dispatcher, nil)
	if err != nil {
		t.Fatal(err)
	}
	connector.now = func() time.Time { return now }
	if err := connector.cycle(context.Background()); err == nil {
		t.Fatal("terminal acknowledgement loss was not retried")
	}
	if dispatcher.calls != 1 {
		t.Fatalf("status loss blocked execution: calls=%d", dispatcher.calls)
	}
	if err := connector.cycle(context.Background()); err != nil {
		t.Fatal(err)
	}
	if dispatcher.calls != 1 {
		t.Fatalf("terminal retry duplicated execution: calls=%d", dispatcher.calls)
	}
}

func TestConnectorUnpairErasesOnlyCloudCredentials(t *testing.T) {
	public, _, _ := ed25519.GenerateKey(rand.Reader)
	root := t.TempDir()
	control := &fakeControlPlane{challenge: "challenge", registration: device.Registration{DeviceID: "dev-1", AccessToken: "access", RefreshToken: "refresh", ExpiresAt: time.Now().Add(time.Hour)}}
	connector, err := NewConnector(Config{DataDir: root, Endpoint: "https://cloud.example", ServerSigningPublicKey: base64.RawStdEncoding.EncodeToString(public)}, control, &countingDispatcher{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := connector.store.Save(control.registration); err != nil {
		t.Fatal(err)
	}
	if err := connector.Unpair(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, "device", "registration.json")); !os.IsNotExist(err) {
		t.Fatalf("registration retained: %v", err)
	}
	if _, err := os.Stat(filepath.Join(root, "device", "identity.key")); err != nil {
		t.Fatalf("identity removed: %v", err)
	}
	reloaded, err := NewConnector(Config{DataDir: root, Endpoint: "https://cloud.example", ServerSigningPublicKey: base64.RawStdEncoding.EncodeToString(public)}, control, &countingDispatcher{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if reloaded.Status().Enabled {
		t.Fatal("unpair did not survive restart")
	}
	if err := reloaded.Pair(context.Background()); err != nil {
		t.Fatal(err)
	}
	if !reloaded.Status().Enabled {
		t.Fatal("pair did not re-enable the connector")
	}
}

func connectorCommand(private ed25519.PrivateKey, now time.Time) device.Command {
	payload := []byte("opaque")
	hash := sha256.Sum256(payload)
	command := device.Command{Version: 1, CommandID: "cmd-1", DeviceID: "dev-1", Type: "cron.execute", AgentID: "a12345", ResourceID: "c12345", ReservationID: "res-1", ScheduledAt: now, IssuedAt: now, ExpiresAt: now.Add(time.Hour), Attempt: 1, PayloadCiphertext: base64.RawStdEncoding.EncodeToString(payload), PayloadSHA256: hex.EncodeToString(hash[:])}
	command.Signature = base64.RawStdEncoding.EncodeToString(ed25519.Sign(private, command.CanonicalPayload()))
	return command
}

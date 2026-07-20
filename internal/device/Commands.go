// <Summary>
// Commands defines protocol-v1 envelopes and verifies canonical signatures,
// device binding, TTL, reservations, and ciphertext integrity before dispatch.
// File: Commands.go
// Types: Command, Validator
// </Summary>
package device

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

type Command struct {
	Version           int       `json:"version"`
	CommandID         string    `json:"command_id"`
	DeviceID          string    `json:"device_id"`
	Type              string    `json:"type"`
	AgentID           string    `json:"agent_id"`
	ResourceID        string    `json:"resource_id"`
	ReservationID     string    `json:"reservation_id"`
	ScheduledAt       time.Time `json:"scheduled_at"`
	IssuedAt          time.Time `json:"issued_at"`
	ExpiresAt         time.Time `json:"expires_at"`
	Attempt           int       `json:"attempt"`
	PayloadCiphertext string    `json:"payload_ciphertext"`
	PayloadSHA256     string    `json:"payload_sha256"`
	Signature         string    `json:"signature"`
}

type Validator struct {
	deviceID string
	server   ed25519.PublicKey
	now      func() time.Time
}

func NewValidator(deviceID string, serverPublicKey string) (*Validator, error) {
	decoded, err := base64.RawStdEncoding.DecodeString(strings.TrimSpace(serverPublicKey))
	if err != nil || len(decoded) != ed25519.PublicKeySize {
		return nil, fmt.Errorf("invalid control-plane signing public key")
	}
	return &Validator{deviceID: deviceID, server: ed25519.PublicKey(decoded), now: func() time.Time { return time.Now().UTC() }}, nil
}

func (v *Validator) Validate(command Command) error {
	if command.Version != 1 {
		return fmt.Errorf("unsupported command version")
	}
	if command.DeviceID != v.deviceID {
		return fmt.Errorf("command targets another device")
	}
	if strings.TrimSpace(command.CommandID) == "" || strings.TrimSpace(command.ResourceID) == "" || strings.TrimSpace(command.ReservationID) == "" {
		return fmt.Errorf("command identity and reservation are required")
	}
	if command.Type != "cron.execute" {
		return fmt.Errorf("unsupported command type")
	}
	if !command.ExpiresAt.After(v.now()) || command.IssuedAt.After(v.now().Add(5*time.Minute)) {
		return fmt.Errorf("command expired or issued in the future")
	}
	payload, err := base64.RawStdEncoding.DecodeString(command.PayloadCiphertext)
	if err != nil {
		return fmt.Errorf("invalid command payload encoding")
	}
	hash := sha256.Sum256(payload)
	if !strings.EqualFold(hex.EncodeToString(hash[:]), command.PayloadSHA256) {
		return fmt.Errorf("command payload checksum mismatch")
	}
	signature, err := base64.RawStdEncoding.DecodeString(command.Signature)
	if err != nil || !ed25519.Verify(v.server, command.CanonicalPayload(), signature) {
		return fmt.Errorf("invalid command signature")
	}
	return nil
}

func (c Command) CanonicalPayload() []byte {
	payload, _ := json.Marshal(struct {
		Version           int       `json:"version"`
		CommandID         string    `json:"command_id"`
		DeviceID          string    `json:"device_id"`
		Type              string    `json:"type"`
		AgentID           string    `json:"agent_id"`
		ResourceID        string    `json:"resource_id"`
		ReservationID     string    `json:"reservation_id"`
		ScheduledAt       time.Time `json:"scheduled_at"`
		IssuedAt          time.Time `json:"issued_at"`
		ExpiresAt         time.Time `json:"expires_at"`
		Attempt           int       `json:"attempt"`
		PayloadCiphertext string    `json:"payload_ciphertext"`
		PayloadSHA256     string    `json:"payload_sha256"`
	}{c.Version, c.CommandID, c.DeviceID, c.Type, c.AgentID, c.ResourceID, c.ReservationID, c.ScheduledAt, c.IssuedAt, c.ExpiresAt, c.Attempt, c.PayloadCiphertext, c.PayloadSHA256})
	return payload
}

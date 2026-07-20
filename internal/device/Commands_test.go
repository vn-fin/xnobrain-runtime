// <Summary>
// Device protocol tests prove stable identity persistence, strict signed-command
// validation, and durable terminal receipts that survive process restart.
// File: Commands_test.go
// Tests: identity, validator rejection matrix, journal replay
// </Summary>
package device

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestDeviceIdentityPersistsWithOwnerOnlyPermissions(t *testing.T) {
	root := t.TempDir()
	first, err := LoadOrCreateIdentity(root)
	if err != nil {
		t.Fatal(err)
	}
	second, err := LoadOrCreateIdentity(root)
	if err != nil {
		t.Fatal(err)
	}
	if first.PublicKey() != second.PublicKey() {
		t.Fatal("device identity rotated on reload")
	}
	info, err := os.Stat(filepath.Join(root, "identity.key"))
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("private key mode = %o", info.Mode().Perm())
	}
	public, _ := base64.RawStdEncoding.DecodeString(first.PublicKey())
	signature, _ := base64.RawStdEncoding.DecodeString(first.Sign([]byte("challenge")))
	if !ed25519.Verify(public, []byte("challenge"), signature) {
		t.Fatal("persisted identity signature is invalid")
	}
}

func TestCommandValidatorRejectsTamperingAndExpiry(t *testing.T) {
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	validator, err := NewValidator("dev-1", base64.RawStdEncoding.EncodeToString(public))
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(1000, 0).UTC()
	validator.now = func() time.Time { return now }
	command := signedCommand(private, now)
	if err := validator.Validate(command); err != nil {
		t.Fatalf("valid command rejected: %v", err)
	}

	tests := map[string]func(*Command){
		"wrong device": func(command *Command) { command.DeviceID = "dev-other" },
		"expired":      func(command *Command) { command.ExpiresAt = now.Add(-time.Second) },
		"future issue": func(command *Command) { command.IssuedAt = now.Add(6 * time.Minute) },
		"altered payload": func(command *Command) {
			command.PayloadCiphertext = base64.RawStdEncoding.EncodeToString([]byte("altered"))
		},
		"missing reservation": func(command *Command) { command.ReservationID = "" },
		"wrong signature": func(command *Command) {
			command.Signature = base64.RawStdEncoding.EncodeToString(make([]byte, ed25519.SignatureSize))
		},
	}
	for name, mutate := range tests {
		t.Run(name, func(t *testing.T) {
			current := command
			mutate(&current)
			if err := validator.Validate(current); err == nil {
				t.Fatal("invalid command accepted")
			}
		})
	}
	if strings.Contains(string(command.CanonicalPayload()), "signature") {
		t.Fatal("canonical signature payload includes the signature field")
	}
}

func TestCommandJournalTerminalReceiptIsReplaySafe(t *testing.T) {
	root := t.TempDir()
	journal, err := NewJournal(root)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := journal.Transition("cmd-1", "received", ""); err != nil {
		t.Fatal(err)
	}
	terminal, err := journal.Transition("cmd-1", "completed", "")
	if err != nil {
		t.Fatal(err)
	}
	replayed, err := journal.Transition("cmd-1", "running", "")
	if err != nil {
		t.Fatal(err)
	}
	if replayed.Status != terminal.Status || replayed.UpdatedAt != terminal.UpdatedAt {
		t.Fatalf("terminal receipt changed on replay: %+v", replayed)
	}
	reloaded, err := NewJournal(root)
	if err != nil {
		t.Fatal(err)
	}
	receipt, exists, err := reloaded.Get("cmd-1")
	if err != nil || !exists || receipt.Status != "completed" {
		t.Fatalf("journal did not survive restart: %+v %v %v", receipt, exists, err)
	}
}

func signedCommand(private ed25519.PrivateKey, now time.Time) Command {
	payload := []byte("opaque payload")
	hash := sha256.Sum256(payload)
	command := Command{Version: 1, CommandID: "cmd-1", DeviceID: "dev-1", Type: "cron.execute", AgentID: "a12345", ResourceID: "c12345", ReservationID: "res-1", ScheduledAt: now, IssuedAt: now, ExpiresAt: now.Add(time.Hour), Attempt: 1, PayloadCiphertext: base64.RawStdEncoding.EncodeToString(payload), PayloadSHA256: hex.EncodeToString(hash[:])}
	command.Signature = base64.RawStdEncoding.EncodeToString(ed25519.Sign(private, command.CanonicalPayload()))
	return command
}

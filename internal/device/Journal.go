// <Summary>
// Journal durably records command transitions before acknowledgements, making
// at-least-once cloud delivery replay-safe across reconnects and process restarts.
// File: Journal.go
// Types: Journal, Receipt
// </Summary>
package device

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sync"
	"time"
)

var commandIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`)

type Receipt struct {
	CommandID  string    `json:"command_id"`
	Status     string    `json:"status"`
	ErrorCode  string    `json:"error_code,omitempty"`
	ReceivedAt time.Time `json:"received_at"`
	UpdatedAt  time.Time `json:"updated_at"`
}

type Journal struct {
	root string
	mu   sync.Mutex
	now  func() time.Time
}

func NewJournal(root string) (*Journal, error) {
	if err := os.MkdirAll(root, 0o700); err != nil {
		return nil, err
	}
	return &Journal{root: root, now: func() time.Time { return time.Now().UTC() }}, nil
}

func (j *Journal) Get(commandID string) (Receipt, bool, error) {
	j.mu.Lock()
	defer j.mu.Unlock()
	return j.get(commandID)
}

func (j *Journal) Transition(commandID string, status string, errorCode string) (Receipt, error) {
	j.mu.Lock()
	defer j.mu.Unlock()
	if !commandIDPattern.MatchString(commandID) {
		return Receipt{}, fmt.Errorf("invalid command id")
	}
	receipt, exists, err := j.get(commandID)
	if err != nil {
		return Receipt{}, err
	}
	now := j.now()
	if !exists {
		receipt = Receipt{CommandID: commandID, ReceivedAt: now}
	}
	if isTerminal(receipt.Status) {
		return receipt, nil
	}
	receipt.Status = status
	receipt.ErrorCode = errorCode
	receipt.UpdatedAt = now
	payload, err := json.MarshalIndent(receipt, "", "  ")
	if err != nil {
		return Receipt{}, err
	}
	payload = append(payload, '\n')
	if err := atomicWrite(filepath.Join(j.root, commandID+".json"), payload, 0o600); err != nil {
		return Receipt{}, err
	}
	return receipt, nil
}

func (j *Journal) get(commandID string) (Receipt, bool, error) {
	if !commandIDPattern.MatchString(commandID) {
		return Receipt{}, false, fmt.Errorf("invalid command id")
	}
	payload, err := os.ReadFile(filepath.Join(j.root, commandID+".json"))
	if os.IsNotExist(err) {
		return Receipt{}, false, nil
	}
	if err != nil {
		return Receipt{}, false, err
	}
	var receipt Receipt
	if err := json.Unmarshal(payload, &receipt); err != nil {
		return Receipt{}, false, err
	}
	return receipt, true, nil
}

func isTerminal(status string) bool {
	return status == "completed" || status == "failed" || status == "expired" || status == "rejected" || status == "cancelled"
}

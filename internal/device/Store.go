// <Summary>
// Store persists cloud registration state and rotating access credentials with
// owner-only permissions, independently from profile and device identity data.
// File: Store.go
// Types: Store, Registration
// </Summary>
package device

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"time"
)

type Registration struct {
	DeviceID     string    `json:"device_id"`
	AccessToken  string    `json:"access_token"`
	RefreshToken string    `json:"refresh_token"`
	ExpiresAt    time.Time `json:"expires_at"`
	Cursor       string    `json:"cursor"`
	RecoveryCode string    `json:"recovery_code,omitempty"`
	Claimed      bool      `json:"claimed"`
}

type Store struct {
	path         string
	disabledPath string
	mu           sync.Mutex
}

func NewStore(root string) (*Store, error) {
	if err := os.MkdirAll(root, 0o700); err != nil {
		return nil, err
	}
	return &Store{path: filepath.Join(root, "registration.json"), disabledPath: filepath.Join(root, "unpaired")}, nil
}

func (s *Store) Load() (Registration, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	payload, err := os.ReadFile(s.path)
	if errors.Is(err, os.ErrNotExist) {
		return Registration{}, false, nil
	}
	if err != nil {
		return Registration{}, false, err
	}
	var registration Registration
	if err := json.Unmarshal(payload, &registration); err != nil {
		return Registration{}, false, err
	}
	return registration, true, nil
}

func (s *Store) Save(registration Registration) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	payload, err := json.MarshalIndent(registration, "", "  ")
	if err != nil {
		return err
	}
	return atomicWrite(s.path, append(payload, '\n'), 0o600)
}

func (s *Store) Clear() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := os.Remove(s.path); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return syncDirectory(filepath.Dir(s.path))
}

func (s *Store) SetEnabled(enabled bool) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if enabled {
		if err := os.Remove(s.disabledPath); err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
		return syncDirectory(filepath.Dir(s.disabledPath))
	}
	return atomicWrite(s.disabledPath, []byte("unpaired\n"), 0o600)
}

func (s *Store) Enabled() (bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	_, err := os.Stat(s.disabledPath)
	if errors.Is(err, os.ErrNotExist) {
		return true, nil
	}
	return false, err
}

func atomicWrite(path string, payload []byte, mode os.FileMode) error {
	temporary, err := os.CreateTemp(filepath.Dir(path), ".open-lumora-device-*")
	if err != nil {
		return err
	}
	temporaryName := temporary.Name()
	defer os.Remove(temporaryName)
	if err := temporary.Chmod(mode); err != nil {
		_ = temporary.Close()
		return err
	}
	if _, err := temporary.Write(payload); err != nil {
		_ = temporary.Close()
		return err
	}
	if err := temporary.Sync(); err != nil {
		_ = temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	if err := os.Rename(temporaryName, path); err != nil {
		return err
	}
	return syncDirectory(filepath.Dir(path))
}

func syncDirectory(path string) error {
	directory, err := os.Open(path)
	if err != nil {
		return err
	}
	defer directory.Close()
	return directory.Sync()
}

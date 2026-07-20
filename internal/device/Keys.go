// <Summary>
// Package device owns the local Ed25519 possession identity. Private key bytes
// are generated once, stored mode 0600 under DATA_DIR/device, and never exposed.
// File: Keys.go
// Types: Identity
// </Summary>
package device

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
)

type Identity struct {
	private ed25519.PrivateKey
	public  ed25519.PublicKey
}

func LoadOrCreateIdentity(root string) (*Identity, error) {
	if err := os.MkdirAll(root, 0o700); err != nil {
		return nil, err
	}
	path := filepath.Join(root, "identity.key")
	payload, err := os.ReadFile(path)
	if err == nil {
		if chmodErr := os.Chmod(path, 0o600); chmodErr != nil {
			return nil, chmodErr
		}
		decoded, decodeErr := base64.RawStdEncoding.DecodeString(string(payload))
		if decodeErr != nil || len(decoded) != ed25519.PrivateKeySize {
			return nil, fmt.Errorf("invalid device private key")
		}
		private := ed25519.PrivateKey(decoded)
		return &Identity{private: private, public: private.Public().(ed25519.PublicKey)}, nil
	}
	if !os.IsNotExist(err) {
		return nil, err
	}
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return nil, err
	}
	if err := atomicWrite(path, []byte(base64.RawStdEncoding.EncodeToString(private)), 0o600); err != nil {
		return nil, err
	}
	return &Identity{private: private, public: public}, nil
}

func (i *Identity) PublicKey() string {
	return base64.RawStdEncoding.EncodeToString(i.public)
}

func (i *Identity) Sign(payload []byte) string {
	return base64.RawStdEncoding.EncodeToString(ed25519.Sign(i.private, payload))
}

// <Summary>
// NewID creates the short, filesystem-safe identifiers used by agents, conversations, runs, and crons.
// </Summary>
package identity

import (
	"crypto/rand"
	"math/big"
)

func NewID() (string, error) {
	const first = "abcdefghijklmnopqrstuvwxyz"
	const rest = "abcdefghijklmnopqrstuvwxyz0123456789"
	result := make([]byte, 6)
	for index := range result {
		alphabet := rest
		if index == 0 {
			alphabet = first
		}
		value, err := rand.Int(rand.Reader, big.NewInt(int64(len(alphabet))))
		if err != nil {
			return "", err
		}
		result[index] = alphabet[value.Int64()]
	}
	return string(result), nil
}

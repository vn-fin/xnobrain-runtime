// <Summary>
// Public sentinel errors keep HTTP behavior stable across Community and
// enterprise repository implementations.
// </Summary>
package contract

import "errors"

var (
	ErrNotFound = errors.New("record not found")
	ErrConflict = errors.New("record already exists")
)

// <Summary>
// Package repositories owns persistence contracts and raw SQL implementations.
// File: Repository.go
// Types:
//   - Repository
//
// </Summary>
package repositories

import "github.com/xno/open-lumora/internal/studio/contract"

var ErrNotFound = contract.ErrNotFound
var ErrConflict = contract.ErrConflict

type Repository = contract.Repository

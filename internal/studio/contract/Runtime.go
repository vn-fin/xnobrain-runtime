// <Summary>
// Package contract defines the public runtime boundary used by Studio composition.
// File: Runtime.go
// Types: Runtime, Event, Result
// </Summary>
package contract

import "context"

type Event struct {
	Type           string         `json:"event"`
	Text           string         `json:"text,omitempty"`
	RunID          string         `json:"run_id,omitempty"`
	PatternKey     string         `json:"pattern_key,omitempty"`
	PatternKeys    []string       `json:"pattern_keys,omitempty"`
	Description    string         `json:"description,omitempty"`
	Command        string         `json:"command,omitempty"`
	AllowPermanent bool           `json:"allow_permanent,omitempty"`
	Payload        map[string]any `json:"-"`
}

type Request struct {
	RunID            string
	ProfilePath      string
	WorkspacePath    string
	ConversationID   string
	RuntimeSessionID string
	Input            string
	Model            string
	Toolsets         []string
}

type Result struct {
	Output           string
	RuntimeSessionID string
}

type Runtime interface {
	Run(ctx context.Context, request Request, emit func(Event)) (Result, error)
	ResolveApproval(ctx context.Context, runID string, choice string, resolveAll bool) error
}

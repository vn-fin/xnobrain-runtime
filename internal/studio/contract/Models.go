// <Summary>
// Package contract defines public, versioned Studio integration contracts.
// File: Models.go
// Types: Agent, AgentConfig, Skill, Snapshot, Conversation, Message, CronJob
// </Summary>
package contract

import "time"

type Agent struct {
	ID          string         `json:"id"`
	UserID      string         `json:"user_id,omitempty"`
	Name        string         `json:"name"`
	Title       string         `json:"title"`
	Description string         `json:"description"`
	Status      string         `json:"status"`
	Config      AgentConfig    `json:"config"`
	CreatedAt   time.Time      `json:"created_at"`
	UpdatedAt   time.Time      `json:"updated_at"`
	Metadata    map[string]any `json:"metadata,omitempty"`
}

type AgentConfig struct {
	Provider            string `json:"provider" yaml:"provider"`
	Model               string `json:"model" yaml:"model"`
	ReasoningEffort     string `json:"reasoning_effort" yaml:"reasoning_effort"`
	ApprovalMode        string `json:"approval_mode" yaml:"approval_mode"`
	SkillsWriteApproval bool   `json:"skills_write_approval" yaml:"skills_write_approval"`
	MemoryWriteApproval bool   `json:"memory_write_approval" yaml:"memory_write_approval"`
}

type ProfileConfig struct {
	OpenLumora ProfileMetadata `yaml:"open_lumora,omitempty"`
	Model      struct {
		Provider string `yaml:"provider,omitempty"`
		Default  string `yaml:"default,omitempty"`
		BaseURL  string `yaml:"base_url,omitempty"`
	} `yaml:"model"`
	Providers map[string]ProviderConfig `yaml:"providers,omitempty"`
	Agent     struct {
		ReasoningEffort string `yaml:"reasoning_effort,omitempty"`
		SystemPrompt    string `yaml:"system_prompt,omitempty"`
	} `yaml:"agent"`
	Approvals struct {
		Mode        string            `yaml:"mode"`
		Persisted   map[string]string `yaml:"persisted,omitempty"`
		LastUpdated string            `yaml:"last_updated,omitempty"`
	} `yaml:"approvals"`
	Skills struct {
		WriteApproval bool     `yaml:"write_approval"`
		Disabled      []string `yaml:"disabled,omitempty"`
		ExternalDirs  []string `yaml:"external_dirs,omitempty"`
	} `yaml:"skills"`
	Memory struct {
		WriteApproval bool `yaml:"write_approval"`
	} `yaml:"memory"`
	Terminal struct {
		Backend string `yaml:"backend"`
		CWD     string `yaml:"cwd"`
	} `yaml:"terminal"`
}

type ProfileMetadata struct {
	SchemaVersion int       `json:"schema_version" yaml:"schema_version"`
	ID            string    `json:"id" yaml:"id"`
	UserID        string    `json:"user_id" yaml:"user_id"`
	Name          string    `json:"name" yaml:"name"`
	Title         string    `json:"title" yaml:"title"`
	Description   string    `json:"description" yaml:"description"`
	Status        string    `json:"status" yaml:"status"`
	CreatedAt     time.Time `json:"created_at" yaml:"created_at"`
	UpdatedAt     time.Time `json:"updated_at" yaml:"updated_at"`
}

type ProviderConfig struct {
	Name                  string                    `yaml:"name"`
	API                   string                    `yaml:"api"`
	APIMode               string                    `yaml:"api_mode"`
	DefaultModel          string                    `yaml:"default_model"`
	Model                 string                    `yaml:"model"`
	KeyEnv                string                    `yaml:"key_env"`
	RequestTimeoutSeconds int                       `yaml:"request_timeout_seconds"`
	Models                map[string]map[string]any `yaml:"models"`
}

type Skill struct {
	SkillID     string `json:"skill_id"`
	Name        string `json:"name"`
	Category    string `json:"category"`
	Description string `json:"description"`
	Enabled     bool   `json:"enabled"`
	Installed   bool   `json:"installed"`
	Path        string `json:"path"`
}

type Snapshot struct {
	ID        string    `json:"id"`
	AgentID   string    `json:"agent_id"`
	Kind      string    `json:"kind"`
	Target    string    `json:"target"`
	Hash      string    `json:"hash"`
	Path      string    `json:"path"`
	CreatedAt time.Time `json:"created_at"`
}

type Conversation struct {
	ID               string    `json:"id"`
	AgentID          string    `json:"agent_id,omitempty"`
	Title            string    `json:"title"`
	Preview          string    `json:"preview"`
	Model            string    `json:"model"`
	Messages         int       `json:"messages"`
	Tools            int       `json:"tools"`
	RuntimeSessionID string    `json:"-"`
	CreatedAt        time.Time `json:"created_at"`
	UpdatedAt        time.Time `json:"updated_at"`
}

type Message struct {
	ID        int64          `json:"id"`
	Role      string         `json:"role"`
	Content   string         `json:"content"`
	Metadata  map[string]any `json:"metadata,omitempty"`
	CreatedAt time.Time      `json:"created_at"`
}

type CronJob struct {
	ID              string     `json:"id" yaml:"id"`
	AgentID         string     `json:"agent_id" yaml:"agent_id"`
	Name            string     `json:"name" yaml:"name"`
	Schedule        string     `json:"schedule" yaml:"schedule"`
	Timezone        string     `json:"timezone" yaml:"timezone"`
	Mode            string     `json:"mode" yaml:"mode"`
	MisfirePolicy   string     `json:"misfire_policy" yaml:"misfire_policy"`
	ReplayLimit     int        `json:"replay_limit,omitempty" yaml:"replay_limit,omitempty"`
	Prompt          string     `json:"prompt" yaml:"prompt"`
	Enabled         bool       `json:"enabled" yaml:"enabled"`
	NextRunAt       *time.Time `json:"next_run_at,omitempty" yaml:"next_run_at,omitempty"`
	LastRunAt       *time.Time `json:"last_run_at,omitempty" yaml:"last_run_at,omitempty"`
	LastEvaluatedAt *time.Time `json:"last_evaluated_at,omitempty" yaml:"last_evaluated_at,omitempty"`
	CreatedAt       time.Time  `json:"created_at" yaml:"created_at"`
	UpdatedAt       time.Time  `json:"updated_at" yaml:"updated_at"`
}

type UsageCounter struct {
	UserID      string    `json:"user_id,omitempty"`
	Resource    string    `json:"resource"`
	PeriodStart time.Time `json:"period_start"`
	Used        int       `json:"used"`
}

type Notification struct {
	ID          string               `json:"id"`
	DedupKey    string               `json:"dedup_key"`
	Type        string               `json:"type"`
	OfflineFrom time.Time            `json:"offline_from"`
	OfflineTo   time.Time            `json:"offline_to"`
	TotalMissed int                  `json:"total_missed"`
	Jobs        []MissedCronSummary  `json:"jobs"`
	Actions     []string             `json:"actions"`
	CreatedAt   time.Time            `json:"created_at"`
	Resolution  *NotificationResolve `json:"resolution,omitempty"`
}

type MissedCronSummary struct {
	CronID          string    `json:"cron_id"`
	CronName        string    `json:"cron_name"`
	Count           int       `json:"count"`
	FirstOccurrence time.Time `json:"first_occurrence"`
	LastOccurrence  time.Time `json:"last_occurrence"`
}

type NotificationResolve struct {
	Action     string    `json:"action"`
	ResolvedAt time.Time `json:"resolved_at"`
}

type Team struct {
	ID              string       `json:"id" yaml:"id"`
	UserID          string       `json:"user_id" yaml:"user_id"`
	Name            string       `json:"name" yaml:"name"`
	OrchestratorID  string       `json:"orchestrator_id" yaml:"orchestrator_id"`
	Members         []TeamMember `json:"members" yaml:"members"`
	SharedWorkspace bool         `json:"shared_workspace" yaml:"shared_workspace"`
	MaxParallel     int          `json:"max_parallel" yaml:"max_parallel"`
	MaxDepth        int          `json:"max_depth" yaml:"max_depth"`
	Enabled         bool         `json:"enabled" yaml:"enabled"`
	CreatedAt       time.Time    `json:"created_at" yaml:"created_at"`
	UpdatedAt       time.Time    `json:"updated_at" yaml:"updated_at"`
}

type TeamMember struct {
	AgentID      string   `json:"agent_id" yaml:"agent_id"`
	Role         string   `json:"role" yaml:"role"`
	AllowedTools []string `json:"allowed_tools" yaml:"allowed_tools"`
	Enabled      bool     `json:"enabled" yaml:"enabled"`
	Diagnostic   string   `json:"diagnostic,omitempty" yaml:"diagnostic,omitempty"`
}

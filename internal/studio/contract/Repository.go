// <Summary>
// Repository is the public persistence boundary implemented by Community files
// and by paid control-plane storage without exposing internal packages.
// </Summary>
package contract

import (
	"context"
	"time"
)

type Repository interface {
	ListAgents(ctx context.Context, userID string) ([]Agent, error)
	CountAgents(ctx context.Context, userID string) (int, error)
	CreateAgent(ctx context.Context, agent Agent) (Agent, error)
	GetAgent(ctx context.Context, userID string, agentID string) (Agent, error)
	UpdateAgent(ctx context.Context, agent Agent) (Agent, error)
	DeleteAgent(ctx context.Context, userID string, agentID string) error

	ListConversations(ctx context.Context, userID string, agentID string) ([]Conversation, error)
	CreateConversation(ctx context.Context, userID string, conversation Conversation) (Conversation, error)
	GetConversation(ctx context.Context, userID string, agentID string, conversationID string) (Conversation, error)
	UpdateConversation(ctx context.Context, userID string, conversation Conversation) (Conversation, error)
	DeleteConversation(ctx context.Context, userID string, agentID string, conversationID string) error
	ListMessages(ctx context.Context, conversationID string) ([]Message, error)
	AddMessage(ctx context.Context, conversationID string, message Message) (Message, error)

	ListCrons(ctx context.Context, userID string) ([]CronJob, error)
	CreateCron(ctx context.Context, userID string, job CronJob) (CronJob, error)
	UpdateCron(ctx context.Context, userID string, job CronJob) (CronJob, error)
	DeleteCron(ctx context.Context, userID string, cronID string) error

	GetUsage(ctx context.Context, userID string, resource string, periodStart time.Time) (UsageCounter, error)
	ReserveUsage(ctx context.Context, userID string, resource string, periodStart time.Time, limit int) (UsageCounter, bool, error)
	ReserveUsageGroup(ctx context.Context, userID string, reservations []UsageReservation) ([]UsageCounter, bool, error)
}

type UsageReservation struct {
	Resource    string
	PeriodStart time.Time
	Limit       int
}

type NotificationStore interface {
	ListNotifications(ctx context.Context, userID string) ([]Notification, error)
	CreateNotification(ctx context.Context, userID string, notification Notification) (Notification, error)
	ResolveNotification(ctx context.Context, userID string, notificationID string, resolution NotificationResolve) (Notification, error)
}

type TeamStore interface {
	ListTeams(ctx context.Context, userID string) ([]Team, error)
	CreateTeam(ctx context.Context, team Team) (Team, error)
	GetTeam(ctx context.Context, userID string, teamID string) (Team, error)
	UpdateTeam(ctx context.Context, team Team) (Team, error)
	DeleteTeam(ctx context.Context, userID string, teamID string) error
}

type EventSink interface {
	Publish(ctx context.Context, event EventEnvelope) error
}

type EventEnvelope struct {
	Type          string         `json:"type"`
	SubjectIDHash string         `json:"subject_id_hash,omitempty"`
	CorrelationID string         `json:"correlation_id,omitempty"`
	Attributes    map[string]any `json:"attributes,omitempty"`
	OccurredAt    time.Time      `json:"occurred_at"`
}

type Clock interface {
	Now() time.Time
}

type Connector interface {
	Start(ctx context.Context) error
	Close() error
}

// <Summary>
// Package repositories owns persistence contracts and raw SQL implementations.
// File: Memory.go
// Functions:
//   - NewMemory() *Memory
//   - In-process Repository implementation for tests and explicit degraded development mode
//
// </Summary>
package repositories

import (
	"context"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/xno/open-lumora/internal/models"
	"github.com/xno/open-lumora/internal/studio/contract"
)

type Memory struct {
	mu            sync.RWMutex
	agents        map[string]models.Agent
	conversations map[string]models.Conversation
	messages      map[string][]models.Message
	crons         map[string]models.CronJob
	usage         map[string]models.UsageCounter
	notifications map[string]models.Notification
	teams         map[string]models.Team
	nextMessageID int64
}

func NewMemory() *Memory {
	return &Memory{
		agents: make(map[string]models.Agent), conversations: make(map[string]models.Conversation),
		messages: make(map[string][]models.Message), crons: make(map[string]models.CronJob), usage: make(map[string]models.UsageCounter), notifications: make(map[string]models.Notification), teams: make(map[string]models.Team), nextMessageID: 1,
	}
}

func (m *Memory) ListNotifications(_ context.Context, userID string) ([]models.Notification, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	result := make([]models.Notification, 0)
	prefix := userID + "\x00"
	for key, notification := range m.notifications {
		if strings.HasPrefix(key, prefix) {
			result = append(result, notification)
		}
	}
	sort.Slice(result, func(left int, right int) bool { return result[left].CreatedAt.After(result[right].CreatedAt) })
	return result, nil
}

func (m *Memory) CreateNotification(_ context.Context, userID string, notification models.Notification) (models.Notification, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	for key, current := range m.notifications {
		if strings.HasPrefix(key, userID+"\x00") && current.DedupKey == notification.DedupKey {
			return current, nil
		}
	}
	m.notifications[userID+"\x00"+notification.ID] = notification
	return notification, nil
}

func (m *Memory) ResolveNotification(_ context.Context, userID string, notificationID string, resolution models.NotificationResolve) (models.Notification, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	key := userID + "\x00" + notificationID
	notification, exists := m.notifications[key]
	if !exists {
		return models.Notification{}, ErrNotFound
	}
	if notification.Resolution == nil {
		notification.Resolution = &resolution
		m.notifications[key] = notification
	}
	return notification, nil
}

func (m *Memory) ListAgents(_ context.Context, userID string) ([]models.Agent, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	result := make([]models.Agent, 0)
	for _, agent := range m.agents {
		if agent.UserID == userID && agent.Status != "deleted" {
			result = append(result, agent)
		}
	}
	sort.Slice(result, func(left int, right int) bool { return result[left].CreatedAt.Before(result[right].CreatedAt) })
	return result, nil
}

func (m *Memory) CountAgents(ctx context.Context, userID string) (int, error) {
	agents, err := m.ListAgents(ctx, userID)
	return len(agents), err
}

func (m *Memory) CreateAgent(_ context.Context, agent models.Agent) (models.Agent, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, exists := m.agents[agent.ID]; exists {
		return models.Agent{}, ErrNotFound
	}
	m.agents[agent.ID] = agent
	return agent, nil
}

func (m *Memory) GetAgent(_ context.Context, userID string, agentID string) (models.Agent, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	agent, exists := m.agents[agentID]
	if !exists || agent.UserID != userID || agent.Status == "deleted" {
		return models.Agent{}, ErrNotFound
	}
	return agent, nil
}

func (m *Memory) UpdateAgent(_ context.Context, agent models.Agent) (models.Agent, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	current, exists := m.agents[agent.ID]
	if !exists || current.UserID != agent.UserID {
		return models.Agent{}, ErrNotFound
	}
	m.agents[agent.ID] = agent
	return agent, nil
}

func (m *Memory) DeleteAgent(_ context.Context, userID string, agentID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	agent, exists := m.agents[agentID]
	if !exists || agent.UserID != userID {
		return ErrNotFound
	}
	agent.Status = "deleted"
	agent.UpdatedAt = time.Now().UTC()
	m.agents[agentID] = agent
	return nil
}

func (m *Memory) ListConversations(_ context.Context, userID string, agentID string) ([]models.Conversation, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	if agent, exists := m.agents[agentID]; !exists || agent.UserID != userID {
		return nil, ErrNotFound
	}
	result := make([]models.Conversation, 0)
	for _, conversation := range m.conversations {
		if conversation.AgentID == agentID {
			conversation.Messages = len(m.messages[conversation.ID])
			result = append(result, conversation)
		}
	}
	sort.Slice(result, func(left int, right int) bool { return result[left].UpdatedAt.After(result[right].UpdatedAt) })
	return result, nil
}

func (m *Memory) CreateConversation(_ context.Context, userID string, conversation models.Conversation) (models.Conversation, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if agent, exists := m.agents[conversation.AgentID]; !exists || agent.UserID != userID {
		return models.Conversation{}, ErrNotFound
	}
	for _, current := range m.conversations {
		if current.AgentID == conversation.AgentID && current.Title == conversation.Title {
			return models.Conversation{}, ErrNotFound
		}
	}
	m.conversations[conversation.ID] = conversation
	return conversation, nil
}

func (m *Memory) GetConversation(_ context.Context, userID string, agentID string, conversationID string) (models.Conversation, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	conversation, exists := m.conversations[conversationID]
	agent, agentExists := m.agents[agentID]
	if !exists || !agentExists || agent.UserID != userID || conversation.AgentID != agentID {
		return models.Conversation{}, ErrNotFound
	}
	conversation.Messages = len(m.messages[conversationID])
	return conversation, nil
}

func (m *Memory) UpdateConversation(_ context.Context, userID string, conversation models.Conversation) (models.Conversation, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	agent, agentExists := m.agents[conversation.AgentID]
	if _, exists := m.conversations[conversation.ID]; !exists || !agentExists || agent.UserID != userID {
		return models.Conversation{}, ErrNotFound
	}
	m.conversations[conversation.ID] = conversation
	return conversation, nil
}

func (m *Memory) DeleteConversation(_ context.Context, userID string, agentID string, conversationID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	agent, agentExists := m.agents[agentID]
	conversation, exists := m.conversations[conversationID]
	if !exists || !agentExists || agent.UserID != userID || conversation.AgentID != agentID {
		return ErrNotFound
	}
	delete(m.conversations, conversationID)
	delete(m.messages, conversationID)
	return nil
}

func (m *Memory) ListMessages(_ context.Context, conversationID string) ([]models.Message, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	if _, exists := m.conversations[conversationID]; !exists {
		return nil, ErrNotFound
	}
	return append([]models.Message(nil), m.messages[conversationID]...), nil
}

func (m *Memory) AddMessage(_ context.Context, conversationID string, message models.Message) (models.Message, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, exists := m.conversations[conversationID]; !exists {
		return models.Message{}, ErrNotFound
	}
	message.ID = m.nextMessageID
	m.nextMessageID++
	m.messages[conversationID] = append(m.messages[conversationID], message)
	conversation := m.conversations[conversationID]
	conversation.UpdatedAt = message.CreatedAt
	if message.Role == "assistant" {
		conversation.Preview = message.Content
	}
	m.conversations[conversationID] = conversation
	return message, nil
}

func (m *Memory) ListCrons(_ context.Context, userID string) ([]models.CronJob, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	result := make([]models.CronJob, 0)
	for _, job := range m.crons {
		if agent, exists := m.agents[job.AgentID]; exists && agent.UserID == userID {
			result = append(result, job)
		}
	}
	return result, nil
}

func (m *Memory) CreateCron(_ context.Context, userID string, job models.CronJob) (models.CronJob, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if agent, exists := m.agents[job.AgentID]; !exists || agent.UserID != userID {
		return models.CronJob{}, ErrNotFound
	}
	m.crons[job.ID] = job
	return job, nil
}

func (m *Memory) UpdateCron(_ context.Context, userID string, job models.CronJob) (models.CronJob, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, exists := m.crons[job.ID]; !exists {
		return models.CronJob{}, ErrNotFound
	}
	if agent, exists := m.agents[job.AgentID]; !exists || agent.UserID != userID {
		return models.CronJob{}, ErrNotFound
	}
	m.crons[job.ID] = job
	return job, nil
}

func (m *Memory) DeleteCron(_ context.Context, userID string, cronID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	job, exists := m.crons[cronID]
	agent, agentExists := m.agents[job.AgentID]
	if !exists || !agentExists || agent.UserID != userID {
		return ErrNotFound
	}
	delete(m.crons, cronID)
	return nil
}

func (m *Memory) GetUsage(_ context.Context, userID string, resource string, periodStart time.Time) (models.UsageCounter, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	key := usageKey(userID, resource, periodStart)
	if counter, exists := m.usage[key]; exists {
		return counter, nil
	}
	return models.UsageCounter{UserID: userID, Resource: resource, PeriodStart: periodStart.UTC(), Used: 0}, nil
}

func (m *Memory) ReserveUsage(_ context.Context, userID string, resource string, periodStart time.Time, limit int) (models.UsageCounter, bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	key := usageKey(userID, resource, periodStart)
	counter := m.usage[key]
	counter.UserID = userID
	counter.Resource = resource
	counter.PeriodStart = periodStart.UTC()
	if limit >= 0 && counter.Used >= limit {
		return counter, false, nil
	}
	counter.Used++
	m.usage[key] = counter
	return counter, true, nil
}

func (m *Memory) ReserveUsageGroup(_ context.Context, userID string, reservations []contract.UsageReservation) ([]models.UsageCounter, bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	counters := make([]models.UsageCounter, len(reservations))
	for index, reservation := range reservations {
		key := usageKey(userID, reservation.Resource, reservation.PeriodStart)
		counter := m.usage[key]
		counter.UserID = userID
		counter.Resource = reservation.Resource
		counter.PeriodStart = reservation.PeriodStart.UTC()
		counters[index] = counter
		if reservation.Limit >= 0 && counter.Used >= reservation.Limit {
			return counters, false, nil
		}
	}
	for index, reservation := range reservations {
		counters[index].Used++
		m.usage[usageKey(userID, reservation.Resource, reservation.PeriodStart)] = counters[index]
	}
	return counters, true, nil
}

func usageKey(userID string, resource string, periodStart time.Time) string {
	return userID + "\x00" + resource + "\x00" + periodStart.UTC().Format(time.RFC3339)
}

// <Summary>
// File is the durable Community repository backed only by files inside DATA_DIR.
// Agent metadata lives in each profile config, cron jobs live below that profile,
// and local usage is atomically persisted in the Community data root.
// </Summary>
package repositories

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/xno/open-lumora/internal/models"
	"github.com/xno/open-lumora/internal/profile"
	"github.com/xno/open-lumora/pkg/studio/contract"
	"gopkg.in/yaml.v3"
)

const communitySchemaVersion = 1

type File struct {
	mu          sync.Mutex
	profiles    *profile.Manager
	dataRoot    string
	diagnostics []profile.Diagnostic
}

type conversationRecord struct {
	Conversation models.Conversation `json:"conversation"`
	Messages     []models.Message    `json:"messages"`
}

type conversationFile struct {
	SchemaVersion int                  `json:"schema_version"`
	NextMessageID int64                `json:"next_message_id"`
	Records       []conversationRecord `json:"records"`
}

type usageFile struct {
	SchemaVersion int                            `json:"schema_version"`
	Counters      map[string]models.UsageCounter `json:"counters"`
}

func NewFile(profiles *profile.Manager) (*File, error) {
	if profiles == nil {
		return nil, fmt.Errorf("profiles manager is required")
	}
	repository := &File{profiles: profiles, dataRoot: profiles.DataRoot()}
	for _, path := range []string{filepath.Join(repository.dataRoot, "usage"), filepath.Join(repository.dataRoot, "notifications"), filepath.Join(repository.dataRoot, "runtime"), filepath.Join(repository.dataRoot, "teams")} {
		if err := os.MkdirAll(path, 0o750); err != nil {
			return nil, fmt.Errorf("create Community data directory: %w", err)
		}
	}
	return repository, nil
}

func (f *File) Diagnostics() []profile.Diagnostic {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]profile.Diagnostic(nil), f.diagnostics...)
}

func (f *File) ListAgents(_ context.Context, userID string) ([]models.Agent, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.listAgentsLocked(userID)
}

func (f *File) CountAgents(_ context.Context, userID string) (int, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	agents, err := f.listAgentsLocked(userID)
	return len(agents), err
}

func (f *File) CreateAgent(_ context.Context, agent models.Agent) (models.Agent, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	metadata, err := f.profiles.ReadAgentMetadata(agent.ID)
	if err != nil {
		return models.Agent{}, err
	}
	if metadata.SchemaVersion != 0 {
		return models.Agent{}, ErrConflict
	}
	if err := f.profiles.WriteAgentMetadata(agent.ID, metadataFromAgent(agent)); err != nil {
		return models.Agent{}, err
	}
	return f.agentFromProfile(agent.ID)
}

func (f *File) GetAgent(_ context.Context, userID string, agentID string) (models.Agent, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	agent, err := f.agentFromProfile(agentID)
	if err != nil || agent.UserID != userID || agent.Status == "deleted" {
		return models.Agent{}, ErrNotFound
	}
	return agent, nil
}

func (f *File) UpdateAgent(_ context.Context, agent models.Agent) (models.Agent, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	current, err := f.agentFromProfile(agent.ID)
	if err != nil || current.UserID != agent.UserID || current.Status == "deleted" {
		return models.Agent{}, ErrNotFound
	}
	agent.CreatedAt = current.CreatedAt
	if err := f.profiles.WriteAgentMetadata(agent.ID, metadataFromAgent(agent)); err != nil {
		return models.Agent{}, err
	}
	return f.agentFromProfile(agent.ID)
}

func (f *File) DeleteAgent(_ context.Context, userID string, agentID string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	agent, err := f.agentFromProfile(agentID)
	if err != nil || agent.UserID != userID || agent.Status == "deleted" {
		return ErrNotFound
	}
	deletedAt := time.Now().UTC()
	agent.Status = "deleted"
	agent.UpdatedAt = deletedAt
	if err := f.profiles.WriteAgentMetadata(agentID, metadataFromAgent(agent)); err != nil {
		return err
	}
	_, err = f.profiles.TrashProfile(agentID, deletedAt)
	return err
}

func (f *File) ListConversations(_ context.Context, userID string, agentID string) ([]models.Conversation, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if err := f.ensureOwned(agentID, userID); err != nil {
		return nil, err
	}
	store, err := f.readConversations(agentID)
	if err != nil {
		return nil, err
	}
	result := make([]models.Conversation, 0, len(store.Records))
	for _, record := range store.Records {
		conversation := record.Conversation
		conversation.Messages = len(record.Messages)
		if len(record.Messages) > 0 {
			for index := len(record.Messages) - 1; index >= 0; index-- {
				if record.Messages[index].Role == "assistant" {
					conversation.Preview = record.Messages[index].Content
					break
				}
			}
		}
		result = append(result, conversation)
	}
	sort.Slice(result, func(left int, right int) bool { return result[left].UpdatedAt.After(result[right].UpdatedAt) })
	return result, nil
}

func (f *File) CreateConversation(_ context.Context, userID string, conversation models.Conversation) (models.Conversation, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if err := f.ensureOwned(conversation.AgentID, userID); err != nil {
		return models.Conversation{}, err
	}
	store, err := f.readConversations(conversation.AgentID)
	if err != nil {
		return models.Conversation{}, err
	}
	for _, current := range store.Records {
		if current.Conversation.ID == conversation.ID || current.Conversation.Title == conversation.Title {
			return models.Conversation{}, ErrConflict
		}
	}
	store.Records = append(store.Records, conversationRecord{Conversation: conversation, Messages: []models.Message{}})
	if err := f.writeConversations(conversation.AgentID, store); err != nil {
		return models.Conversation{}, err
	}
	return conversation, nil
}

func (f *File) GetConversation(_ context.Context, userID string, agentID string, conversationID string) (models.Conversation, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if err := f.ensureOwned(agentID, userID); err != nil {
		return models.Conversation{}, err
	}
	store, err := f.readConversations(agentID)
	if err != nil {
		return models.Conversation{}, err
	}
	for _, record := range store.Records {
		if record.Conversation.ID == conversationID {
			conversation := record.Conversation
			conversation.Messages = len(record.Messages)
			return conversation, nil
		}
	}
	return models.Conversation{}, ErrNotFound
}

func (f *File) UpdateConversation(_ context.Context, userID string, conversation models.Conversation) (models.Conversation, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if err := f.ensureOwned(conversation.AgentID, userID); err != nil {
		return models.Conversation{}, err
	}
	store, err := f.readConversations(conversation.AgentID)
	if err != nil {
		return models.Conversation{}, err
	}
	for index := range store.Records {
		if store.Records[index].Conversation.ID == conversation.ID {
			conversation.CreatedAt = store.Records[index].Conversation.CreatedAt
			store.Records[index].Conversation = conversation
			if err := f.writeConversations(conversation.AgentID, store); err != nil {
				return models.Conversation{}, err
			}
			return conversation, nil
		}
	}
	return models.Conversation{}, ErrNotFound
}

func (f *File) DeleteConversation(_ context.Context, userID string, agentID string, conversationID string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if err := f.ensureOwned(agentID, userID); err != nil {
		return err
	}
	store, err := f.readConversations(agentID)
	if err != nil {
		return err
	}
	for index := range store.Records {
		if store.Records[index].Conversation.ID == conversationID {
			store.Records = append(store.Records[:index], store.Records[index+1:]...)
			return f.writeConversations(agentID, store)
		}
	}
	return ErrNotFound
}

func (f *File) ListMessages(_ context.Context, conversationID string) ([]models.Message, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	_, record, err := f.findConversation(conversationID)
	if err != nil {
		return nil, err
	}
	return append([]models.Message(nil), record.Messages...), nil
}

func (f *File) AddMessage(_ context.Context, conversationID string, message models.Message) (models.Message, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	agentID, _, err := f.findConversation(conversationID)
	if err != nil {
		return models.Message{}, err
	}
	store, err := f.readConversations(agentID)
	if err != nil {
		return models.Message{}, err
	}
	for index := range store.Records {
		if store.Records[index].Conversation.ID != conversationID {
			continue
		}
		message.ID = store.NextMessageID
		store.NextMessageID++
		store.Records[index].Messages = append(store.Records[index].Messages, message)
		store.Records[index].Conversation.UpdatedAt = message.CreatedAt
		if err := f.writeConversations(agentID, store); err != nil {
			return models.Message{}, err
		}
		return message, nil
	}
	return models.Message{}, ErrNotFound
}

func (f *File) ListCrons(_ context.Context, userID string) ([]models.CronJob, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	agents, err := f.listAgentsLocked(userID)
	if err != nil {
		return nil, err
	}
	result := make([]models.CronJob, 0)
	for _, agent := range agents {
		jobsPath, _ := f.profiles.ProfilePath(agent.ID)
		entries, readErr := os.ReadDir(filepath.Join(jobsPath, "cron", "jobs"))
		if errors.Is(readErr, os.ErrNotExist) {
			continue
		}
		if readErr != nil {
			return nil, readErr
		}
		for _, entry := range entries {
			if entry.IsDir() || filepath.Ext(entry.Name()) != ".yaml" {
				continue
			}
			job, decodeErr := readYAML[models.CronJob](filepath.Join(jobsPath, "cron", "jobs", entry.Name()))
			if decodeErr != nil {
				f.diagnostics = append(f.diagnostics, profile.Diagnostic{AgentID: agent.ID, Path: entry.Name(), Code: "invalid_cron", Message: decodeErr.Error()})
				continue
			}
			result = append(result, job)
		}
	}
	sort.Slice(result, func(left int, right int) bool { return result[left].CreatedAt.Before(result[right].CreatedAt) })
	return result, nil
}

func (f *File) CreateCron(_ context.Context, userID string, job models.CronJob) (models.CronJob, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if err := f.ensureOwned(job.AgentID, userID); err != nil {
		return models.CronJob{}, err
	}
	path, err := f.cronPath(job.AgentID, job.ID)
	if err != nil {
		return models.CronJob{}, err
	}
	if _, err := os.Stat(path); err == nil {
		return models.CronJob{}, ErrConflict
	}
	if err := f.profiles.MutateProfile(job.AgentID, func(string) error { return writeYAML(path, job) }); err != nil {
		return models.CronJob{}, err
	}
	return job, nil
}

func (f *File) UpdateCron(_ context.Context, userID string, job models.CronJob) (models.CronJob, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if err := f.ensureOwned(job.AgentID, userID); err != nil {
		return models.CronJob{}, err
	}
	path, err := f.cronPath(job.AgentID, job.ID)
	if err != nil {
		return models.CronJob{}, err
	}
	if _, err := os.Stat(path); errors.Is(err, os.ErrNotExist) {
		return models.CronJob{}, ErrNotFound
	} else if err != nil {
		return models.CronJob{}, err
	}
	if err := f.profiles.MutateProfile(job.AgentID, func(string) error { return writeYAML(path, job) }); err != nil {
		return models.CronJob{}, err
	}
	return job, nil
}

func (f *File) DeleteCron(_ context.Context, userID string, cronID string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	agents, err := f.listAgentsLocked(userID)
	if err != nil {
		return err
	}
	for _, agent := range agents {
		path, pathErr := f.cronPath(agent.ID, cronID)
		if pathErr != nil {
			continue
		}
		if _, statErr := os.Stat(path); statErr == nil {
			return f.profiles.MutateProfile(agent.ID, func(string) error {
				if err := os.Remove(path); err != nil {
					return err
				}
				return syncDirectory(filepath.Dir(path))
			})
		} else if !errors.Is(statErr, os.ErrNotExist) {
			return statErr
		}
	}
	return ErrNotFound
}

func (f *File) GetUsage(_ context.Context, userID string, resource string, periodStart time.Time) (models.UsageCounter, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.readUsage(userID, resource, periodStart)
}

func (f *File) ReserveUsage(_ context.Context, userID string, resource string, periodStart time.Time, limit int) (models.UsageCounter, bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	store, err := f.readUsageFile(userID)
	if err != nil {
		return models.UsageCounter{}, false, err
	}
	counter := usageCounter(store, userID, resource, periodStart)
	if limit >= 0 && counter.Used >= limit {
		return counter, false, nil
	}
	counter.Used++
	store.Counters[usageCounterKey(resource, periodStart)] = counter
	if err := f.writeUsageFile(userID, store); err != nil {
		return models.UsageCounter{}, false, err
	}
	return counter, true, nil
}

func (f *File) ReserveUsageGroup(_ context.Context, userID string, reservations []contract.UsageReservation) ([]models.UsageCounter, bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	store, err := f.readUsageFile(userID)
	if err != nil {
		return nil, false, err
	}
	counters := make([]models.UsageCounter, len(reservations))
	for index, reservation := range reservations {
		counter := usageCounter(store, userID, reservation.Resource, reservation.PeriodStart)
		counters[index] = counter
		if reservation.Limit >= 0 && counter.Used >= reservation.Limit {
			return counters, false, nil
		}
	}
	for index, reservation := range reservations {
		counters[index].Used++
		store.Counters[usageCounterKey(reservation.Resource, reservation.PeriodStart)] = counters[index]
	}
	if err := f.writeUsageFile(userID, store); err != nil {
		return nil, false, err
	}
	return counters, true, nil
}

func (f *File) ListNotifications(_ context.Context, userID string) ([]models.Notification, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	directory := f.notificationsPath(userID)
	entries, err := os.ReadDir(directory)
	if errors.Is(err, os.ErrNotExist) {
		return []models.Notification{}, nil
	}
	if err != nil {
		return nil, err
	}
	result := make([]models.Notification, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		notification, readErr := readJSON[models.Notification](filepath.Join(directory, entry.Name()))
		if readErr != nil {
			f.diagnostics = append(f.diagnostics, profile.Diagnostic{Path: entry.Name(), Code: "invalid_notification", Message: readErr.Error()})
			continue
		}
		result = append(result, notification)
	}
	sort.Slice(result, func(left int, right int) bool { return result[left].CreatedAt.After(result[right].CreatedAt) })
	return result, nil
}

func (f *File) CreateNotification(_ context.Context, userID string, notification models.Notification) (models.Notification, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if !safeComponent(notification.ID) {
		return models.Notification{}, fmt.Errorf("invalid notification id")
	}
	directory := f.notificationsPath(userID)
	entries, err := os.ReadDir(directory)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return models.Notification{}, err
	}
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		current, readErr := readJSON[models.Notification](filepath.Join(directory, entry.Name()))
		if readErr == nil && current.DedupKey == notification.DedupKey {
			return current, nil
		}
	}
	if err := writeJSON(filepath.Join(directory, notification.ID+".json"), notification); err != nil {
		return models.Notification{}, err
	}
	return notification, nil
}

func (f *File) ResolveNotification(_ context.Context, userID string, notificationID string, resolution models.NotificationResolve) (models.Notification, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if !safeComponent(notificationID) {
		return models.Notification{}, ErrNotFound
	}
	path := filepath.Join(f.notificationsPath(userID), notificationID+".json")
	notification, err := readJSON[models.Notification](path)
	if errors.Is(err, os.ErrNotExist) {
		return models.Notification{}, ErrNotFound
	}
	if err != nil {
		return models.Notification{}, err
	}
	if notification.Resolution == nil {
		notification.Resolution = &resolution
		if err := writeJSON(path, notification); err != nil {
			return models.Notification{}, err
		}
	}
	return notification, nil
}

func (f *File) listAgentsLocked(userID string) ([]models.Agent, error) {
	ids, err := f.profiles.ProfileIDs()
	if err != nil {
		return nil, err
	}
	f.diagnostics = f.profiles.Diagnostics()
	result := make([]models.Agent, 0, len(ids))
	for _, id := range ids {
		agent, readErr := f.agentFromProfile(id)
		if readErr != nil {
			f.diagnostics = append(f.diagnostics, profile.Diagnostic{AgentID: id, Code: "invalid_agent_metadata", Message: readErr.Error()})
			continue
		}
		if agent.UserID == userID && agent.Status != "deleted" {
			result = append(result, agent)
		}
	}
	sort.Slice(result, func(left int, right int) bool { return result[left].CreatedAt.Before(result[right].CreatedAt) })
	return result, nil
}

func (f *File) agentFromProfile(agentID string) (models.Agent, error) {
	metadata, err := f.profiles.ReadAgentMetadata(agentID)
	if err != nil {
		return models.Agent{}, err
	}
	if metadata.SchemaVersion == 0 {
		metadata, err = f.migrateMetadata(agentID)
		if err != nil {
			return models.Agent{}, err
		}
	}
	if metadata.SchemaVersion != communitySchemaVersion || metadata.ID != agentID {
		return models.Agent{}, fmt.Errorf("unsupported agent metadata schema")
	}
	config, err := f.profiles.ReadConfig(agentID)
	if err != nil {
		return models.Agent{}, err
	}
	return models.Agent{ID: metadata.ID, UserID: metadata.UserID, Name: metadata.Name, Title: metadata.Title, Description: metadata.Description, Status: metadata.Status, Config: config, CreatedAt: metadata.CreatedAt, UpdatedAt: metadata.UpdatedAt}, nil
}

func (f *File) migrateMetadata(agentID string) (models.ProfileMetadata, error) {
	profilePath, err := f.profiles.ProfilePath(agentID)
	if err != nil {
		return models.ProfileMetadata{}, err
	}
	var legacy struct {
		Name string `yaml:"name"`
	}
	payload, readErr := os.ReadFile(filepath.Join(profilePath, "profile.yaml"))
	if readErr == nil {
		_ = yaml.Unmarshal(payload, &legacy)
	}
	name := strings.TrimSpace(legacy.Name)
	if name == "" {
		name = agentID
	}
	info, err := os.Stat(profilePath)
	if err != nil {
		return models.ProfileMetadata{}, err
	}
	now := info.ModTime().UTC()
	metadata := models.ProfileMetadata{SchemaVersion: communitySchemaVersion, ID: agentID, UserID: "local", Name: name, Title: name, Status: "active", CreatedAt: now, UpdatedAt: now}
	if err := f.profiles.WriteAgentMetadata(agentID, metadata); err != nil {
		return models.ProfileMetadata{}, err
	}
	return metadata, nil
}

func (f *File) ensureOwned(agentID string, userID string) error {
	agent, err := f.agentFromProfile(agentID)
	if err != nil || agent.UserID != userID || agent.Status == "deleted" {
		return ErrNotFound
	}
	return nil
}

func (f *File) conversationsPath(agentID string) (string, error) {
	profilePath, err := f.profiles.ProfilePath(agentID)
	if err != nil {
		return "", err
	}
	return filepath.Join(profilePath, "sessions", "studio-index.json"), nil
}

func (f *File) readConversations(agentID string) (conversationFile, error) {
	path, err := f.conversationsPath(agentID)
	if err != nil {
		return conversationFile{}, err
	}
	store, err := readJSON[conversationFile](path)
	if errors.Is(err, os.ErrNotExist) {
		return conversationFile{SchemaVersion: communitySchemaVersion, NextMessageID: 1, Records: []conversationRecord{}}, nil
	}
	if err != nil {
		return conversationFile{}, err
	}
	if store.SchemaVersion != communitySchemaVersion {
		return conversationFile{}, fmt.Errorf("unsupported conversation schema")
	}
	if store.NextMessageID < 1 {
		store.NextMessageID = 1
	}
	return store, nil
}

func (f *File) writeConversations(agentID string, store conversationFile) error {
	path, err := f.conversationsPath(agentID)
	if err != nil {
		return err
	}
	return f.profiles.MutateProfile(agentID, func(string) error { return writeJSON(path, store) })
}

func (f *File) findConversation(conversationID string) (string, conversationRecord, error) {
	ids, err := f.profiles.ProfileIDs()
	if err != nil {
		return "", conversationRecord{}, err
	}
	for _, agentID := range ids {
		store, readErr := f.readConversations(agentID)
		if readErr != nil {
			continue
		}
		for _, record := range store.Records {
			if record.Conversation.ID == conversationID {
				return agentID, record, nil
			}
		}
	}
	return "", conversationRecord{}, ErrNotFound
}

func (f *File) cronPath(agentID string, cronID string) (string, error) {
	if !safeComponent(cronID) {
		return "", fmt.Errorf("invalid cron id")
	}
	profilePath, err := f.profiles.ProfilePath(agentID)
	if err != nil {
		return "", err
	}
	return filepath.Join(profilePath, "cron", "jobs", cronID+".yaml"), nil
}

func (f *File) readUsage(userID string, resource string, periodStart time.Time) (models.UsageCounter, error) {
	store, err := f.readUsageFile(userID)
	if err != nil {
		return models.UsageCounter{}, err
	}
	return usageCounter(store, userID, resource, periodStart), nil
}

func (f *File) readUsageFile(userID string) (usageFile, error) {
	store, err := readJSON[usageFile](f.usagePath(userID))
	if errors.Is(err, os.ErrNotExist) {
		return usageFile{SchemaVersion: communitySchemaVersion, Counters: make(map[string]models.UsageCounter)}, nil
	}
	if err != nil {
		return usageFile{}, err
	}
	if store.SchemaVersion != communitySchemaVersion {
		return usageFile{}, fmt.Errorf("unsupported usage schema")
	}
	if store.Counters == nil {
		store.Counters = make(map[string]models.UsageCounter)
	}
	return store, nil
}

func (f *File) writeUsageFile(userID string, store usageFile) error {
	return writeJSON(f.usagePath(userID), store)
}

func (f *File) usagePath(userID string) string {
	digest := sha256.Sum256([]byte(userID))
	return filepath.Join(f.dataRoot, "usage", hex.EncodeToString(digest[:]), "counters.json")
}

func (f *File) notificationsPath(userID string) string {
	digest := sha256.Sum256([]byte(userID))
	return filepath.Join(f.dataRoot, "notifications", hex.EncodeToString(digest[:]))
}

func usageCounter(store usageFile, userID string, resource string, periodStart time.Time) models.UsageCounter {
	key := usageCounterKey(resource, periodStart)
	if counter, exists := store.Counters[key]; exists {
		return counter
	}
	return models.UsageCounter{UserID: userID, Resource: resource, PeriodStart: periodStart.UTC()}
}

func usageCounterKey(resource string, periodStart time.Time) string {
	return resource + "\x00" + periodStart.UTC().Format(time.RFC3339)
}

func metadataFromAgent(agent models.Agent) models.ProfileMetadata {
	return models.ProfileMetadata{SchemaVersion: communitySchemaVersion, ID: agent.ID, UserID: agent.UserID, Name: agent.Name, Title: agent.Title, Description: agent.Description, Status: agent.Status, CreatedAt: agent.CreatedAt.UTC(), UpdatedAt: agent.UpdatedAt.UTC()}
}

func safeComponent(value string) bool {
	if value == "" || value == "." || value == ".." {
		return false
	}
	return !strings.ContainsAny(value, "/\\\x00")
}

func readJSON[T any](path string) (T, error) {
	var result T
	payload, err := os.ReadFile(path)
	if err != nil {
		return result, err
	}
	if err := json.Unmarshal(payload, &result); err != nil {
		return result, fmt.Errorf("decode %s: %w", path, err)
	}
	return result, nil
}

func readYAML[T any](path string) (T, error) {
	var result T
	payload, err := os.ReadFile(path)
	if err != nil {
		return result, err
	}
	if err := yaml.Unmarshal(payload, &result); err != nil {
		return result, fmt.Errorf("decode %s: %w", path, err)
	}
	return result, nil
}

func writeJSON(path string, value any) error {
	payload, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	payload = append(payload, '\n')
	return atomicWrite(path, payload, 0o640)
}

func writeYAML(path string, value any) error {
	payload, err := yaml.Marshal(value)
	if err != nil {
		return err
	}
	return atomicWrite(path, payload, 0o640)
}

func atomicWrite(path string, payload []byte, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), ".open-lumora-write-*")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
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
	if err := os.Rename(temporaryPath, path); err != nil {
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

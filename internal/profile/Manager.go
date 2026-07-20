// <Summary>
// Package profile owns safe, file-backed agent profiles and immutable snapshots.
// File: Manager.go
// Functions:
//   - NewManager(root string) (*Manager, error)
//   - (*Manager).Create(agentID string, displayName string) (models.AgentConfig, error)
//   - (*Manager).ReadConfig(agentID string) (models.AgentConfig, error)
//   - (*Manager).UpdateConfig(agentID string, patch ConfigPatch) (models.AgentConfig, error)
//   - (*Manager).PersistApproval(agentID string, subsystem string, choice string) (models.AgentConfig, error)
//
// </Summary>
package profile

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/xno/open-lumora/internal/models"
	"gopkg.in/yaml.v3"
)

const defaultInstructions = `# Agent Profile Instructions

This profile is private to one Open Lumora agent.

- Treat $HERMES_HOME as the only profile root.
- Create or improve skills only at $HERMES_HOME/skills/<skill-id>/SKILL.md.
- Never write skills to a shared, root, workspace, current-directory, or user-home skills folder.
- Store durable memory only in $HERMES_HOME/memories/MEMORY.md or USER.md.
- Open Lumora snapshots memory and skills after every successful mutation. Do not edit snapshots.
- Respect memory.write_approval and skills.write_approval from $HERMES_HOME/config.yaml.
`

var (
	agentIDPattern = regexp.MustCompile(`^[a-z][a-z0-9]{5}$`)
	skillIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`)
)

type Manager struct {
	root         string
	profilesRoot string
	rootProfile  string
	now          func() time.Time
	configMu     sync.RWMutex
	barrier      sync.RWMutex
	diagnostics  []Diagnostic
}

type Diagnostic struct {
	AgentID string `json:"agent_id"`
	Path    string `json:"path"`
	Code    string `json:"code"`
	Message string `json:"message"`
}

type ConfigPatch struct {
	Provider            *string
	Model               *string
	ReasoningEffort     *string
	ApprovalMode        *string
	SkillsWriteApproval *bool
	MemoryWriteApproval *bool
	SystemPrompt        *string
}

func NewManager(root string) (*Manager, error) {
	absRoot, err := filepath.Abs(root)
	if err != nil {
		return nil, fmt.Errorf("resolve data root: %w", err)
	}
	manager := &Manager{
		root:         absRoot,
		profilesRoot: filepath.Join(absRoot, "profiles"),
		rootProfile:  filepath.Join(absRoot, "root"),
		now:          func() time.Time { return time.Now().UTC() },
	}
	for _, path := range []string{manager.root, manager.profilesRoot, manager.rootProfile, filepath.Join(manager.rootProfile, "skills")} {
		if err := os.MkdirAll(path, 0o750); err != nil {
			return nil, fmt.Errorf("create profile directory: %w", err)
		}
	}
	if _, err := os.Stat(filepath.Join(manager.rootProfile, "config.yaml")); errors.Is(err, os.ErrNotExist) {
		config := defaultProfileConfig("")
		if err := manager.writeYAML(filepath.Join(manager.rootProfile, "config.yaml"), config); err != nil {
			return nil, err
		}
	} else if err == nil {
		config, readErr := readYAML[models.ProfileConfig](filepath.Join(manager.rootProfile, "config.yaml"))
		if readErr != nil {
			return nil, readErr
		}
		normalizeNineRouter(&config)
		if writeErr := manager.writeYAML(filepath.Join(manager.rootProfile, "config.yaml"), config); writeErr != nil {
			return nil, writeErr
		}
	}
	entries, err := os.ReadDir(manager.profilesRoot)
	if err != nil {
		return nil, fmt.Errorf("read existing profiles: %w", err)
	}
	for _, entry := range entries {
		if !entry.IsDir() || !agentIDPattern.MatchString(entry.Name()) {
			continue
		}
		configPath := filepath.Join(manager.profilesRoot, entry.Name(), "config.yaml")
		config, readErr := readYAML[models.ProfileConfig](configPath)
		if errors.Is(readErr, os.ErrNotExist) {
			continue
		}
		if readErr != nil {
			manager.diagnostics = append(manager.diagnostics, Diagnostic{AgentID: entry.Name(), Path: configPath, Code: "invalid_profile_config", Message: readErr.Error()})
			continue
		}
		normalizeNineRouter(&config)
		if writeErr := manager.writeYAML(configPath, config); writeErr != nil {
			return nil, fmt.Errorf("migrate profile %s: %w", entry.Name(), writeErr)
		}
	}
	return manager, nil
}

func (m *Manager) RootProfile() string { return m.rootProfile }

func (m *Manager) DataRoot() string { return m.root }

func (m *Manager) Diagnostics() []Diagnostic {
	m.configMu.RLock()
	defer m.configMu.RUnlock()
	return append([]Diagnostic(nil), m.diagnostics...)
}

func (m *Manager) ProfileIDs() ([]string, error) {
	m.barrier.RLock()
	defer m.barrier.RUnlock()
	return m.profileIDs()
}

func (m *Manager) profileIDs() ([]string, error) {
	entries, err := os.ReadDir(m.profilesRoot)
	if err != nil {
		return nil, err
	}
	ids := make([]string, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() && agentIDPattern.MatchString(entry.Name()) {
			ids = append(ids, entry.Name())
		}
	}
	sort.Strings(ids)
	return ids, nil
}

func (m *Manager) PublishStagedProfiles(staged map[string]string) error {
	m.barrier.Lock()
	defer m.barrier.Unlock()
	ids := make([]string, 0, len(staged))
	for id := range staged {
		if !agentIDPattern.MatchString(id) {
			return fmt.Errorf("invalid staged agent id")
		}
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		if info, err := os.Lstat(staged[id]); err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("invalid staged profile %s", id)
		}
		if _, err := os.Stat(filepath.Join(m.profilesRoot, id)); err == nil {
			return fmt.Errorf("agent profile %s already exists", id)
		} else if !errors.Is(err, os.ErrNotExist) {
			return err
		}
	}
	published := make([]string, 0, len(ids))
	for _, id := range ids {
		target := filepath.Join(m.profilesRoot, id)
		if err := os.Rename(staged[id], target); err != nil {
			for index := len(published) - 1; index >= 0; index-- {
				rollbackID := published[index]
				_ = os.Rename(filepath.Join(m.profilesRoot, rollbackID), staged[rollbackID])
			}
			return err
		}
		published = append(published, id)
	}
	return syncDirectory(m.profilesRoot)
}

func (m *Manager) UnpublishProfiles(staged map[string]string) error {
	m.barrier.Lock()
	defer m.barrier.Unlock()
	ids := make([]string, 0, len(staged))
	for id := range staged {
		if !agentIDPattern.MatchString(id) {
			return fmt.Errorf("invalid published agent id")
		}
		ids = append(ids, id)
	}
	sort.Strings(ids)
	moved := make([]string, 0, len(ids))
	for _, id := range ids {
		target := staged[id]
		if _, err := os.Lstat(target); err == nil {
			return fmt.Errorf("rollback target for %s already exists", id)
		} else if !errors.Is(err, os.ErrNotExist) {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o750); err != nil {
			return err
		}
		live := filepath.Join(m.profilesRoot, id)
		if err := os.Rename(live, target); err != nil {
			for index := len(moved) - 1; index >= 0; index-- {
				rollbackID := moved[index]
				_ = os.Rename(staged[rollbackID], filepath.Join(m.profilesRoot, rollbackID))
			}
			return err
		}
		moved = append(moved, id)
	}
	return syncDirectory(m.profilesRoot)
}

func (m *Manager) SnapshotProfile(agentID string, read func(profileRoot string) error) error {
	if read == nil {
		return fmt.Errorf("profile snapshot reader is required")
	}
	m.barrier.RLock()
	defer m.barrier.RUnlock()
	root, err := m.ProfilePath(agentID)
	if err != nil {
		return err
	}
	return read(root)
}

func (m *Manager) MutateProfile(agentID string, mutate func(profileRoot string) error) error {
	if mutate == nil {
		return fmt.Errorf("profile mutation is required")
	}
	m.barrier.Lock()
	defer m.barrier.Unlock()
	root, err := m.ProfilePath(agentID)
	if err != nil {
		return err
	}
	return mutate(root)
}

func (m *Manager) ProfilePath(agentID string) (string, error) {
	if !agentIDPattern.MatchString(agentID) {
		return "", fmt.Errorf("invalid agent id")
	}
	return filepath.Join(m.profilesRoot, agentID), nil
}

func (m *Manager) Create(agentID string, displayName string) (models.AgentConfig, error) {
	profilePath, err := m.ProfilePath(agentID)
	if err != nil {
		return models.AgentConfig{}, err
	}
	if _, err := os.Stat(profilePath); err == nil {
		return models.AgentConfig{}, fmt.Errorf("agent profile already exists")
	}
	for _, name := range []string{"skills", "memories", "workspace", "sessions", "cron", "logs", "snapshots", "home"} {
		if err := os.MkdirAll(filepath.Join(profilePath, name), 0o750); err != nil {
			return models.AgentConfig{}, fmt.Errorf("create profile state: %w", err)
		}
	}
	compatibilityPath := filepath.Join(profilePath, "home", ".hermes")
	if err := os.Symlink(profilePath, compatibilityPath); err != nil && !errors.Is(err, os.ErrExist) {
		return models.AgentConfig{}, fmt.Errorf("create profile compatibility link: %w", err)
	}
	config, err := readYAML[models.ProfileConfig](filepath.Join(m.rootProfile, "config.yaml"))
	if err != nil {
		return models.AgentConfig{}, fmt.Errorf("read root profile defaults: %w", err)
	}
	config.Terminal.Backend = "local"
	config.Terminal.CWD = filepath.Join(profilePath, "workspace")
	if config.Approvals.Persisted == nil {
		config.Approvals.Persisted = map[string]string{}
	}
	normalizeNineRouter(&config)
	if err := m.writeYAML(filepath.Join(profilePath, "config.yaml"), config); err != nil {
		return models.AgentConfig{}, err
	}
	if err := atomicWrite(filepath.Join(profilePath, "AGENTS.md"), []byte(defaultInstructions), 0o640); err != nil {
		return models.AgentConfig{}, err
	}
	if err := atomicWrite(filepath.Join(profilePath, "workspace", "AGENTS.md"), []byte(defaultInstructions), 0o640); err != nil {
		return models.AgentConfig{}, err
	}
	if err := atomicWrite(filepath.Join(profilePath, "profile.yaml"), []byte("name: "+yamlScalar(displayName)+"\n"), 0o640); err != nil {
		return models.AgentConfig{}, err
	}
	for _, file := range []string{"MEMORY.md", "USER.md"} {
		if err := atomicWrite(filepath.Join(profilePath, "memories", file), []byte(""), 0o640); err != nil {
			return models.AgentConfig{}, err
		}
	}
	sharedSkills := filepath.Join(m.rootProfile, "skills")
	if err := filepath.WalkDir(sharedSkills, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil || entry.IsDir() || entry.Name() != "SKILL.md" {
			return walkErr
		}
		relative, relErr := filepath.Rel(sharedSkills, filepath.Dir(path))
		if relErr != nil || strings.Contains(relative, string(os.PathSeparator)+"..") {
			return relErr
		}
		payload, readErr := os.ReadFile(path)
		if readErr != nil {
			return readErr
		}
		_, _, writeErr := m.WriteSkill(agentID, filepath.Base(relative), string(payload))
		return writeErr
	}); err != nil {
		return models.AgentConfig{}, fmt.Errorf("copy root skill defaults: %w", err)
	}
	return publicConfig(config), nil
}

func defaultProfileConfig(workspace string) models.ProfileConfig {
	var config models.ProfileConfig
	config.Model.Default = "auto"
	config.Agent.ReasoningEffort = "medium"
	config.Approvals.Mode = "manual"
	config.Approvals.Persisted = map[string]string{}
	config.Skills.WriteApproval = true
	config.Memory.WriteApproval = true
	config.Terminal.Backend = "local"
	config.Terminal.CWD = workspace
	normalizeNineRouter(&config)
	return config
}

func (m *Manager) ReadConfig(agentID string) (models.AgentConfig, error) {
	m.configMu.RLock()
	defer m.configMu.RUnlock()
	config, err := m.readProfileConfig(agentID)
	if err != nil {
		return models.AgentConfig{}, err
	}
	return publicConfig(config), nil
}

func (m *Manager) ReadRootConfig() (models.AgentConfig, error) {
	m.configMu.RLock()
	defer m.configMu.RUnlock()
	config, err := readYAML[models.ProfileConfig](filepath.Join(m.rootProfile, "config.yaml"))
	if err != nil {
		return models.AgentConfig{}, err
	}
	return publicConfig(config), nil
}

func (m *Manager) UpdateRootConfig(patch ConfigPatch) (models.AgentConfig, error) {
	m.barrier.Lock()
	defer m.barrier.Unlock()
	m.configMu.Lock()
	defer m.configMu.Unlock()
	path := filepath.Join(m.rootProfile, "config.yaml")
	config, err := readYAML[models.ProfileConfig](path)
	if err != nil {
		return models.AgentConfig{}, err
	}
	applyConfigPatch(&config, patch)
	if err := m.writeYAML(path, config); err != nil {
		return models.AgentConfig{}, err
	}
	return publicConfig(config), nil
}

func (m *Manager) UpdateConfig(agentID string, patch ConfigPatch) (models.AgentConfig, error) {
	m.barrier.Lock()
	defer m.barrier.Unlock()
	m.configMu.Lock()
	defer m.configMu.Unlock()
	path, err := m.profileFile(agentID, "config.yaml")
	if err != nil {
		return models.AgentConfig{}, err
	}
	config, err := readYAML[models.ProfileConfig](path)
	if err != nil {
		return models.AgentConfig{}, err
	}
	applyConfigPatch(&config, patch)
	if err := m.writeYAML(path, config); err != nil {
		return models.AgentConfig{}, err
	}
	return publicConfig(config), nil
}

func (m *Manager) PersistApproval(agentID string, subsystem string, choice string) (models.AgentConfig, error) {
	if choice != "session" && choice != "always" {
		return m.ReadConfig(agentID)
	}
	if subsystem != "memory_write" && subsystem != "skills_write" {
		return models.AgentConfig{}, fmt.Errorf("unsupported persistent approval subsystem")
	}
	m.barrier.Lock()
	defer m.barrier.Unlock()
	m.configMu.Lock()
	defer m.configMu.Unlock()
	path, err := m.profileFile(agentID, "config.yaml")
	if err != nil {
		return models.AgentConfig{}, err
	}
	config, err := readYAML[models.ProfileConfig](path)
	if err != nil {
		return models.AgentConfig{}, err
	}
	if config.Approvals.Persisted == nil {
		config.Approvals.Persisted = map[string]string{}
	}
	config.Approvals.Persisted[subsystem] = "allow"
	config.Approvals.LastUpdated = m.now().Format(time.RFC3339Nano)
	if subsystem == "memory_write" {
		config.Memory.WriteApproval = false
	} else {
		config.Skills.WriteApproval = false
	}
	if err := m.writeYAML(path, config); err != nil {
		return models.AgentConfig{}, err
	}
	return publicConfig(config), nil
}

func (m *Manager) ReadAgentMetadata(agentID string) (models.ProfileMetadata, error) {
	m.configMu.RLock()
	defer m.configMu.RUnlock()
	config, err := m.readProfileConfig(agentID)
	if err != nil {
		return models.ProfileMetadata{}, err
	}
	return config.OpenLumora, nil
}

func (m *Manager) WriteAgentMetadata(agentID string, metadata models.ProfileMetadata) error {
	m.barrier.Lock()
	defer m.barrier.Unlock()
	m.configMu.Lock()
	defer m.configMu.Unlock()
	path, err := m.profileFile(agentID, "config.yaml")
	if err != nil {
		return err
	}
	config, err := readYAML[models.ProfileConfig](path)
	if err != nil {
		return err
	}
	config.OpenLumora = metadata
	return m.writeYAML(path, config)
}

func (m *Manager) TrashProfile(agentID string, deletedAt time.Time) (string, error) {
	m.barrier.Lock()
	defer m.barrier.Unlock()
	m.configMu.Lock()
	defer m.configMu.Unlock()
	profilePath, err := m.ProfilePath(agentID)
	if err != nil {
		return "", err
	}
	trashRoot := filepath.Join(m.root, "trash", "profiles")
	if err := os.MkdirAll(trashRoot, 0o750); err != nil {
		return "", err
	}
	target := filepath.Join(trashRoot, agentID+"-"+deletedAt.UTC().Format("20060102T150405.000000000Z"))
	if err := os.Rename(profilePath, target); err != nil {
		return "", err
	}
	if err := syncDirectory(trashRoot); err != nil {
		return "", err
	}
	return target, nil
}

func applyConfigPatch(config *models.ProfileConfig, patch ConfigPatch) {
	_ = patch.Provider
	if patch.Model != nil && strings.TrimSpace(*patch.Model) != "" {
		config.Model.Default = strings.TrimSpace(*patch.Model)
	}
	if patch.ReasoningEffort != nil && strings.TrimSpace(*patch.ReasoningEffort) != "" {
		config.Agent.ReasoningEffort = strings.TrimSpace(*patch.ReasoningEffort)
	}
	if patch.ApprovalMode != nil && strings.TrimSpace(*patch.ApprovalMode) != "" {
		config.Approvals.Mode = strings.TrimSpace(*patch.ApprovalMode)
	}
	if patch.SkillsWriteApproval != nil {
		config.Skills.WriteApproval = *patch.SkillsWriteApproval
		if *patch.SkillsWriteApproval {
			delete(config.Approvals.Persisted, "skills_write")
		}
	}
	if patch.MemoryWriteApproval != nil {
		config.Memory.WriteApproval = *patch.MemoryWriteApproval
		if *patch.MemoryWriteApproval {
			delete(config.Approvals.Persisted, "memory_write")
		}
	}
	if patch.SystemPrompt != nil {
		config.Agent.SystemPrompt = *patch.SystemPrompt
	}
	normalizeNineRouter(config)
}

func normalizeNineRouter(config *models.ProfileConfig) {
	if strings.TrimSpace(config.Model.Default) == "" {
		config.Model.Default = "auto"
	}
	config.Model.Provider = "custom:nine-router"
	config.Model.BaseURL = "http://127.0.0.1:20128/v1"
	config.Providers = map[string]models.ProviderConfig{
		"nine-router": {
			Name: "9Router", API: "http://127.0.0.1:20128/v1", APIMode: "chat_completions",
			DefaultModel: config.Model.Default, Model: config.Model.Default,
			KeyEnv: "NINE_ROUTER_API_KEY", RequestTimeoutSeconds: 1800,
			Models: map[string]map[string]any{config.Model.Default: {}},
		},
	}
}

func publicConfig(config models.ProfileConfig) models.AgentConfig {
	return models.AgentConfig{
		Provider:            "nine-router",
		Model:               config.Model.Default,
		ReasoningEffort:     config.Agent.ReasoningEffort,
		ApprovalMode:        config.Approvals.Mode,
		SkillsWriteApproval: config.Skills.WriteApproval,
		MemoryWriteApproval: config.Memory.WriteApproval,
	}
}

func (m *Manager) readProfileConfig(agentID string) (models.ProfileConfig, error) {
	path, err := m.profileFile(agentID, "config.yaml")
	if err != nil {
		return models.ProfileConfig{}, err
	}
	return readYAML[models.ProfileConfig](path)
}

func (m *Manager) profileFile(agentID string, parts ...string) (string, error) {
	root, err := m.ProfilePath(agentID)
	if err != nil {
		return "", err
	}
	path := filepath.Join(append([]string{root}, parts...)...)
	cleanRoot := filepath.Clean(root)
	cleanPath := filepath.Clean(path)
	if cleanPath != cleanRoot && !strings.HasPrefix(cleanPath, cleanRoot+string(os.PathSeparator)) {
		return "", fmt.Errorf("path escapes agent profile")
	}
	return cleanPath, nil
}

func (m *Manager) writeYAML(path string, value any) error {
	payload, err := yaml.Marshal(value)
	if err != nil {
		return fmt.Errorf("encode config: %w", err)
	}
	return atomicWrite(path, payload, 0o640)
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

func atomicWrite(path string, payload []byte, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	temp, err := os.CreateTemp(filepath.Dir(path), ".open-lumora-write-*")
	if err != nil {
		return err
	}
	tempPath := temp.Name()
	defer os.Remove(tempPath)
	if err := temp.Chmod(mode); err != nil {
		temp.Close()
		return err
	}
	if _, err := temp.Write(payload); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Sync(); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	if err := os.Rename(tempPath, path); err != nil {
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

func yamlScalar(value string) string {
	payload, _ := yaml.Marshal(value)
	return strings.TrimSpace(string(payload))
}

func contentHash(payload []byte) string {
	sum := sha256.Sum256(payload)
	return hex.EncodeToString(sum[:])
}

func uniqueSorted(values []string) []string {
	seen := make(map[string]struct{}, len(values))
	result := make([]string, 0, len(values))
	for _, value := range values {
		if _, exists := seen[value]; exists {
			continue
		}
		seen[value] = struct{}{}
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}

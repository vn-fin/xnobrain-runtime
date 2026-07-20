// <Summary>
// Package profile owns safe, file-backed agent profiles and immutable snapshots.
// File: Content.go
// Functions:
//   - (*Manager).WriteSkill(agentID string, skillID string, content string) (models.Skill, models.Snapshot, error)
//   - (*Manager).InstallSharedSkill(agentID string, skillID string, name string, category string) ([]models.Skill, error)
//   - (*Manager).ListSkills(agentID string) ([]models.Skill, error)
//   - (*Manager).ReadMemory(agentID string) (map[string]string, error)
//   - (*Manager).WriteMemory(agentID string, target string, content string, appendMode bool) (map[string]string, models.Snapshot, error)
//
// </Summary>
package profile

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/xno/open-lumora/internal/models"
	"gopkg.in/yaml.v3"
)

type skillFrontmatter struct {
	Name        string `yaml:"name"`
	Description string `yaml:"description"`
	Category    string `yaml:"category"`
}

func (m *Manager) WriteSkill(agentID string, skillID string, content string) (models.Skill, models.Snapshot, error) {
	m.barrier.Lock()
	defer m.barrier.Unlock()
	return m.writeSkill(agentID, skillID, content)
}

func (m *Manager) writeSkill(agentID string, skillID string, content string) (models.Skill, models.Snapshot, error) {
	if !skillIDPattern.MatchString(skillID) {
		return models.Skill{}, models.Snapshot{}, fmt.Errorf("invalid skill id")
	}
	if strings.TrimSpace(content) == "" {
		return models.Skill{}, models.Snapshot{}, fmt.Errorf("skill content is required")
	}
	skillPath, err := m.profileFile(agentID, "skills", skillID, "SKILL.md")
	if err != nil {
		return models.Skill{}, models.Snapshot{}, err
	}
	if err := atomicWrite(skillPath, []byte(content), 0o640); err != nil {
		return models.Skill{}, models.Snapshot{}, err
	}
	snapshot, err := m.snapshot(agentID, "skills", skillID, []byte(content))
	if err != nil {
		return models.Skill{}, models.Snapshot{}, err
	}
	config, err := m.readProfileConfig(agentID)
	if err != nil {
		return models.Skill{}, models.Snapshot{}, err
	}
	return describeSkill(filepath.Dir(skillPath), skillID, config), snapshot, nil
}

func (m *Manager) InstallSharedSkill(agentID string, skillID string, name string, category string) ([]models.Skill, error) {
	if !skillIDPattern.MatchString(skillID) {
		return nil, fmt.Errorf("invalid skill id")
	}
	sharedPath := filepath.Join(m.rootProfile, "skills", skillID, "SKILL.md")
	payload, err := os.ReadFile(sharedPath)
	if errors.Is(err, os.ErrNotExist) {
		if strings.TrimSpace(name) == "" {
			name = skillID
		}
		payload = []byte(fmt.Sprintf("---\nname: %s\ndescription: User-created skill\ncategory: %s\n---\n\n# %s\n\nDescribe the workflow and constraints for this skill.\n", yamlScalar(name), yamlScalar(category), name))
	} else if err != nil {
		return nil, err
	}
	if _, _, err := m.WriteSkill(agentID, skillID, string(payload)); err != nil {
		return nil, err
	}
	return m.ListSkills(agentID)
}

func (m *Manager) ListSkills(agentID string) ([]models.Skill, error) {
	root, err := m.profileFile(agentID, "skills")
	if err != nil {
		return nil, err
	}
	config, err := m.readProfileConfig(agentID)
	if err != nil {
		return nil, err
	}
	result := make([]models.Skill, 0)
	err = filepath.WalkDir(root, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() || entry.Name() != "SKILL.md" {
			return nil
		}
		relative, relErr := filepath.Rel(root, filepath.Dir(path))
		if relErr != nil {
			return relErr
		}
		result = append(result, describeSkill(filepath.Dir(path), filepath.Base(relative), config))
		return nil
	})
	if errors.Is(err, os.ErrNotExist) {
		return result, nil
	}
	if err != nil {
		return nil, err
	}
	sort.Slice(result, func(left int, right int) bool { return result[left].SkillID < result[right].SkillID })
	return result, nil
}

func (m *Manager) SetSkillEnabled(agentID string, skillID string, enabled bool) ([]models.Skill, error) {
	m.barrier.Lock()
	defer m.barrier.Unlock()
	if !skillIDPattern.MatchString(skillID) {
		return nil, fmt.Errorf("invalid skill id")
	}
	skillPath, err := m.profileFile(agentID, "skills", skillID, "SKILL.md")
	if err != nil {
		return nil, err
	}
	if _, err := os.Stat(skillPath); err != nil {
		return nil, fmt.Errorf("skill not found")
	}
	configPath, err := m.profileFile(agentID, "config.yaml")
	if err != nil {
		return nil, err
	}
	config, err := readYAML[models.ProfileConfig](configPath)
	if err != nil {
		return nil, err
	}
	disabled := make([]string, 0, len(config.Skills.Disabled)+1)
	for _, current := range config.Skills.Disabled {
		if current != skillID {
			disabled = append(disabled, current)
		}
	}
	if !enabled {
		disabled = append(disabled, skillID)
	}
	config.Skills.Disabled = uniqueSorted(disabled)
	if err := m.writeYAML(configPath, config); err != nil {
		return nil, err
	}
	return m.ListSkills(agentID)
}

func (m *Manager) RemoveSkill(agentID string, skillID string) ([]models.Skill, error) {
	m.barrier.Lock()
	defer m.barrier.Unlock()
	if !skillIDPattern.MatchString(skillID) {
		return nil, fmt.Errorf("invalid skill id")
	}
	skillDir, err := m.profileFile(agentID, "skills", skillID)
	if err != nil {
		return nil, err
	}
	payload, err := os.ReadFile(filepath.Join(skillDir, "SKILL.md"))
	if err != nil {
		return nil, fmt.Errorf("skill not found")
	}
	if _, err := m.snapshot(agentID, "skills", skillID, payload); err != nil {
		return nil, err
	}
	if err := os.RemoveAll(skillDir); err != nil {
		return nil, err
	}
	return m.setSkillEnabledIfPresent(agentID, skillID, true)
}

func (m *Manager) SetSkillEnabledIfPresent(agentID string, skillID string, enabled bool) ([]models.Skill, error) {
	m.barrier.Lock()
	defer m.barrier.Unlock()
	return m.setSkillEnabledIfPresent(agentID, skillID, enabled)
}

func (m *Manager) setSkillEnabledIfPresent(agentID string, skillID string, enabled bool) ([]models.Skill, error) {
	configPath, err := m.profileFile(agentID, "config.yaml")
	if err != nil {
		return nil, err
	}
	config, err := readYAML[models.ProfileConfig](configPath)
	if err != nil {
		return nil, err
	}
	disabled := make([]string, 0, len(config.Skills.Disabled))
	for _, current := range config.Skills.Disabled {
		if current != skillID || !enabled {
			disabled = append(disabled, current)
		}
	}
	config.Skills.Disabled = uniqueSorted(disabled)
	if err := m.writeYAML(configPath, config); err != nil {
		return nil, err
	}
	return m.ListSkills(agentID)
}

func describeSkill(directory string, fallbackID string, config models.ProfileConfig) models.Skill {
	metadata := skillFrontmatter{Name: fallbackID, Category: "skills"}
	payload, err := os.ReadFile(filepath.Join(directory, "SKILL.md"))
	if err == nil && strings.HasPrefix(string(payload), "---") {
		parts := strings.SplitN(string(payload), "---", 3)
		if len(parts) == 3 {
			_ = yaml.Unmarshal([]byte(parts[1]), &metadata)
		}
	}
	if strings.TrimSpace(metadata.Name) == "" {
		metadata.Name = fallbackID
	}
	if strings.TrimSpace(metadata.Category) == "" {
		metadata.Category = "skills"
	}
	enabled := true
	for _, disabled := range config.Skills.Disabled {
		if disabled == fallbackID || disabled == metadata.Name {
			enabled = false
			break
		}
	}
	return models.Skill{
		SkillID: fallbackID, Name: metadata.Name, Category: metadata.Category,
		Description: metadata.Description, Enabled: enabled, Installed: true, Path: filepath.ToSlash(filepath.Join("skills", fallbackID)),
	}
}

func (m *Manager) ReadMemory(agentID string) (map[string]string, error) {
	result := map[string]string{"memory": "", "user": ""}
	for key, file := range map[string]string{"memory": "MEMORY.md", "user": "USER.md"} {
		path, err := m.profileFile(agentID, "memories", file)
		if err != nil {
			return nil, err
		}
		payload, err := os.ReadFile(path)
		if err != nil && !errors.Is(err, os.ErrNotExist) {
			return nil, err
		}
		result[key] = string(payload)
	}
	return result, nil
}

func (m *Manager) WriteMemory(agentID string, target string, content string, appendMode bool) (map[string]string, models.Snapshot, error) {
	m.barrier.Lock()
	defer m.barrier.Unlock()
	files := map[string]string{"memory": "MEMORY.md", "user": "USER.md"}
	file, exists := files[target]
	if !exists {
		return nil, models.Snapshot{}, fmt.Errorf("memory target must be memory or user")
	}
	path, err := m.profileFile(agentID, "memories", file)
	if err != nil {
		return nil, models.Snapshot{}, err
	}
	payload := []byte(content)
	if appendMode {
		current, readErr := os.ReadFile(path)
		if readErr != nil && !errors.Is(readErr, os.ErrNotExist) {
			return nil, models.Snapshot{}, readErr
		}
		if len(current) > 0 && !strings.HasSuffix(string(current), "\n") {
			current = append(current, '\n')
		}
		payload = append(current, payload...)
	}
	if err := atomicWrite(path, payload, 0o640); err != nil {
		return nil, models.Snapshot{}, err
	}
	snapshot, err := m.snapshot(agentID, "memory", target, payload)
	if err != nil {
		return nil, models.Snapshot{}, err
	}
	memory, err := m.ReadMemory(agentID)
	return memory, snapshot, err
}

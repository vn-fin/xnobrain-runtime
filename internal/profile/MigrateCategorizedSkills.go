// <Summary>
// Migrates legacy Hermes category folders into Open Lumora's canonical flat
// per-agent skill layout without changing skill content or support files.
// File: MigrateCategorizedSkills.go
// Functions:
//   - (*Manager).migrateCategorizedSkills(agentID string) error
//
// </Summary>
package profile

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
)

func (m *Manager) migrateCategorizedSkills(agentID string) error {
	skillsRoot, err := m.profileFile(agentID, "skills")
	if err != nil {
		return err
	}
	candidates := make([]string, 0)
	err = filepath.WalkDir(skillsRoot, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() || entry.Name() != "SKILL.md" {
			return nil
		}
		relative, relErr := filepath.Rel(skillsRoot, filepath.Dir(path))
		if relErr != nil {
			return relErr
		}
		if filepath.Dir(relative) != "." {
			candidates = append(candidates, filepath.Dir(path))
		}
		return nil
	})
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}

	sort.Strings(candidates)
	for _, source := range candidates {
		skillID := filepath.Base(source)
		if !skillIDPattern.MatchString(skillID) {
			m.diagnostics = append(m.diagnostics, Diagnostic{
				AgentID: agentID,
				Path:    source,
				Code:    "invalid_categorized_skill",
				Message: "legacy categorized skill has an invalid skill id",
			})
			continue
		}
		target := filepath.Join(skillsRoot, skillID)
		if _, statErr := os.Lstat(target); statErr == nil {
			m.diagnostics = append(m.diagnostics, Diagnostic{
				AgentID: agentID,
				Path:    source,
				Code:    "categorized_skill_conflict",
				Message: fmt.Sprintf("canonical skill %s already exists", skillID),
			})
			continue
		} else if !errors.Is(statErr, os.ErrNotExist) {
			return statErr
		}
		if err := os.Rename(source, target); err != nil {
			return err
		}
		for parent := filepath.Dir(source); parent != skillsRoot; parent = filepath.Dir(parent) {
			if removeErr := os.Remove(parent); removeErr != nil {
				if errors.Is(removeErr, os.ErrNotExist) {
					continue
				}
				break
			}
		}
	}
	return syncDirectory(skillsRoot)
}

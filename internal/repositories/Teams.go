// <Summary>
// Teams persists user-owned agent teams as atomic YAML documents below DATA_DIR/teams.
// Invalid documents are skipped with repository diagnostics so one damaged team cannot
// prevent the remaining local configuration from loading.
// </Summary>
package repositories

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"github.com/xno/open-lumora/internal/models"
	"github.com/xno/open-lumora/internal/profile"
)

func (f *File) ListTeams(_ context.Context, userID string) ([]models.Team, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	entries, err := os.ReadDir(f.teamsPath())
	if errors.Is(err, os.ErrNotExist) {
		return []models.Team{}, nil
	}
	if err != nil {
		return nil, err
	}
	result := make([]models.Team, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".yaml" {
			continue
		}
		team, readErr := readYAML[models.Team](filepath.Join(f.teamsPath(), entry.Name()))
		if readErr != nil || !safeComponent(team.ID) || team.ID+".yaml" != entry.Name() {
			message := "team id does not match its file"
			if readErr != nil {
				message = readErr.Error()
			}
			f.diagnostics = append(f.diagnostics, profile.Diagnostic{Path: entry.Name(), Code: "invalid_team", Message: message})
			continue
		}
		if team.UserID == userID {
			result = append(result, team)
		}
	}
	sort.Slice(result, func(left int, right int) bool { return result[left].CreatedAt.Before(result[right].CreatedAt) })
	return result, nil
}

func (f *File) CreateTeam(_ context.Context, team models.Team) (models.Team, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	path, err := f.teamPath(team.ID)
	if err != nil {
		return models.Team{}, err
	}
	if _, err := os.Stat(path); err == nil {
		return models.Team{}, ErrConflict
	} else if !errors.Is(err, os.ErrNotExist) {
		return models.Team{}, err
	}
	if err := writeYAML(path, team); err != nil {
		return models.Team{}, err
	}
	return team, nil
}

func (f *File) GetTeam(_ context.Context, userID string, teamID string) (models.Team, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	path, err := f.teamPath(teamID)
	if err != nil {
		return models.Team{}, ErrNotFound
	}
	team, err := readYAML[models.Team](path)
	if errors.Is(err, os.ErrNotExist) || err == nil && team.UserID != userID {
		return models.Team{}, ErrNotFound
	}
	return team, err
}

func (f *File) UpdateTeam(_ context.Context, team models.Team) (models.Team, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	path, err := f.teamPath(team.ID)
	if err != nil {
		return models.Team{}, ErrNotFound
	}
	current, err := readYAML[models.Team](path)
	if errors.Is(err, os.ErrNotExist) || err == nil && current.UserID != team.UserID {
		return models.Team{}, ErrNotFound
	}
	if err != nil {
		return models.Team{}, err
	}
	team.CreatedAt = current.CreatedAt
	if err := writeYAML(path, team); err != nil {
		return models.Team{}, err
	}
	return team, nil
}

func (f *File) DeleteTeam(_ context.Context, userID string, teamID string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	path, err := f.teamPath(teamID)
	if err != nil {
		return ErrNotFound
	}
	team, err := readYAML[models.Team](path)
	if errors.Is(err, os.ErrNotExist) || err == nil && team.UserID != userID {
		return ErrNotFound
	}
	if err != nil {
		return err
	}
	if err := os.Remove(path); err != nil {
		return err
	}
	return syncDirectory(f.teamsPath())
}

func (f *File) teamsPath() string { return filepath.Join(f.dataRoot, "teams") }

func (f *File) teamPath(teamID string) (string, error) {
	if !safeComponent(teamID) {
		return "", fmt.Errorf("invalid team id %q", teamID)
	}
	return filepath.Join(f.teamsPath(), teamID+".yaml"), nil
}

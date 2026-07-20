// <Summary>
// MemoryTeams implements the optional TeamStore contract for isolated tests and
// explicit in-process development runs.
// </Summary>
package repositories

import (
	"context"
	"sort"

	"github.com/xno/open-lumora/internal/models"
)

func (m *Memory) ListTeams(_ context.Context, userID string) ([]models.Team, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	result := make([]models.Team, 0)
	for _, team := range m.teams {
		if team.UserID == userID {
			result = append(result, team)
		}
	}
	sort.Slice(result, func(left int, right int) bool { return result[left].CreatedAt.Before(result[right].CreatedAt) })
	return result, nil
}

func (m *Memory) CreateTeam(_ context.Context, team models.Team) (models.Team, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, exists := m.teams[team.ID]; exists {
		return models.Team{}, ErrConflict
	}
	m.teams[team.ID] = team
	return team, nil
}

func (m *Memory) GetTeam(_ context.Context, userID string, teamID string) (models.Team, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	team, exists := m.teams[teamID]
	if !exists || team.UserID != userID {
		return models.Team{}, ErrNotFound
	}
	return team, nil
}

func (m *Memory) UpdateTeam(_ context.Context, team models.Team) (models.Team, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	current, exists := m.teams[team.ID]
	if !exists || current.UserID != team.UserID {
		return models.Team{}, ErrNotFound
	}
	team.CreatedAt = current.CreatedAt
	m.teams[team.ID] = team
	return team, nil
}

func (m *Memory) DeleteTeam(_ context.Context, userID string, teamID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	team, exists := m.teams[teamID]
	if !exists || team.UserID != userID {
		return ErrNotFound
	}
	delete(m.teams, teamID)
	return nil
}

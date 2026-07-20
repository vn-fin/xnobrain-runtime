// <Summary>
// Package profile owns safe, file-backed agent profiles and immutable snapshots.
// File: Snapshots.go
// Functions:
//   - (*Manager).ListSnapshots(agentID string, kind string) ([]models.Snapshot, error)
//   - (*Manager).RestoreSnapshot(agentID string, snapshotID string) (models.Snapshot, error)
//   - (*Manager).ReconcileSnapshots(agentID string) error
//
// </Summary>
package profile

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/xno/open-lumora/internal/models"
)

type snapshotManifest struct {
	ID        string    `json:"id"`
	AgentID   string    `json:"agent_id"`
	Kind      string    `json:"kind"`
	Target    string    `json:"target"`
	Hash      string    `json:"hash"`
	File      string    `json:"file"`
	CreatedAt time.Time `json:"created_at"`
}

func (m *Manager) snapshot(agentID string, kind string, target string, payload []byte) (models.Snapshot, error) {
	if kind != "memory" && kind != "skills" {
		return models.Snapshot{}, fmt.Errorf("invalid snapshot kind")
	}
	if !skillIDPattern.MatchString(target) && target != "memory" && target != "user" {
		return models.Snapshot{}, fmt.Errorf("invalid snapshot target")
	}
	hash := contentHash(payload)
	id := fmt.Sprintf("%s-%s", m.now().Format("20060102T150405.000000000Z"), hash[:12])
	directory, err := m.profileFile(agentID, "snapshots", kind, target, id)
	if err != nil {
		return models.Snapshot{}, err
	}
	fileName := "content.md"
	if kind == "skills" {
		fileName = "SKILL.md"
	} else if target == "memory" {
		fileName = "MEMORY.md"
	} else {
		fileName = "USER.md"
	}
	if err := os.MkdirAll(directory, 0o750); err != nil {
		return models.Snapshot{}, err
	}
	if err := atomicWrite(filepath.Join(directory, fileName), payload, 0o440); err != nil {
		return models.Snapshot{}, err
	}
	manifest := snapshotManifest{ID: id, AgentID: agentID, Kind: kind, Target: target, Hash: hash, File: fileName, CreatedAt: m.now()}
	manifestPayload, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return models.Snapshot{}, err
	}
	manifestPayload = append(manifestPayload, '\n')
	if err := atomicWrite(filepath.Join(directory, "snapshot.json"), manifestPayload, 0o440); err != nil {
		return models.Snapshot{}, err
	}
	return snapshotModel(manifest, filepath.Join(directory, fileName)), nil
}

func (m *Manager) ListSnapshots(agentID string, kind string) ([]models.Snapshot, error) {
	root, err := m.profileFile(agentID, "snapshots")
	if err != nil {
		return nil, err
	}
	result := make([]models.Snapshot, 0)
	err = filepath.WalkDir(root, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() || entry.Name() != "snapshot.json" {
			return nil
		}
		payload, readErr := os.ReadFile(path)
		if readErr != nil {
			return readErr
		}
		var manifest snapshotManifest
		if decodeErr := json.Unmarshal(payload, &manifest); decodeErr != nil {
			return decodeErr
		}
		if kind != "" && manifest.Kind != kind {
			return nil
		}
		result = append(result, snapshotModel(manifest, filepath.Join(filepath.Dir(path), manifest.File)))
		return nil
	})
	if errors.Is(err, os.ErrNotExist) {
		return result, nil
	}
	if err != nil {
		return nil, err
	}
	sort.Slice(result, func(left int, right int) bool { return result[left].CreatedAt.After(result[right].CreatedAt) })
	return result, nil
}

func (m *Manager) RestoreSnapshot(agentID string, snapshotID string) (models.Snapshot, error) {
	if strings.Contains(snapshotID, "/") || strings.Contains(snapshotID, `\`) || snapshotID == "" {
		return models.Snapshot{}, fmt.Errorf("invalid snapshot id")
	}
	snapshots, err := m.ListSnapshots(agentID, "")
	if err != nil {
		return models.Snapshot{}, err
	}
	for _, current := range snapshots {
		if current.ID != snapshotID {
			continue
		}
		payload, readErr := os.ReadFile(current.Path)
		if readErr != nil {
			return models.Snapshot{}, readErr
		}
		if current.Kind == "skills" {
			_, snapshot, writeErr := m.WriteSkill(agentID, current.Target, string(payload))
			return snapshot, writeErr
		}
		_, snapshot, writeErr := m.WriteMemory(agentID, current.Target, string(payload), false)
		return snapshot, writeErr
	}
	return models.Snapshot{}, fmt.Errorf("snapshot not found")
}

func (m *Manager) ReconcileSnapshots(agentID string) error {
	m.barrier.Lock()
	defer m.barrier.Unlock()
	skills, err := m.ListSkills(agentID)
	if err != nil {
		return err
	}
	existing, err := m.ListSnapshots(agentID, "")
	if err != nil {
		return err
	}
	hashes := make(map[string]struct{}, len(existing))
	for _, snapshot := range existing {
		hashes[snapshot.Kind+":"+snapshot.Target+":"+snapshot.Hash] = struct{}{}
	}
	for _, skill := range skills {
		path, pathErr := m.profileFile(agentID, skill.Path, "SKILL.md")
		if pathErr != nil {
			return pathErr
		}
		payload, readErr := os.ReadFile(path)
		if readErr != nil {
			return readErr
		}
		key := "skills:" + skill.SkillID + ":" + contentHash(payload)
		if _, exists := hashes[key]; !exists {
			if _, snapshotErr := m.snapshot(agentID, "skills", skill.SkillID, payload); snapshotErr != nil {
				return snapshotErr
			}
		}
	}
	memory, err := m.ReadMemory(agentID)
	if err != nil {
		return err
	}
	for target, content := range memory {
		payload := []byte(content)
		key := "memory:" + target + ":" + contentHash(payload)
		if _, exists := hashes[key]; !exists && len(payload) > 0 {
			if _, snapshotErr := m.snapshot(agentID, "memory", target, payload); snapshotErr != nil {
				return snapshotErr
			}
		}
	}
	return nil
}

func snapshotModel(manifest snapshotManifest, path string) models.Snapshot {
	return models.Snapshot{
		ID: manifest.ID, AgentID: manifest.AgentID, Kind: manifest.Kind, Target: manifest.Target,
		Hash: manifest.Hash, Path: path, CreatedAt: manifest.CreatedAt,
	}
}

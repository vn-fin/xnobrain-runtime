// <Summary>
// Package profile owns safe, file-backed agent profiles and immutable snapshots.
// File: Workspace.go
// Functions:
//   - (*Manager).ListWorkspace(agentID string, relative string) ([]WorkspaceEntry, error)
//   - (*Manager).ReadWorkspace(agentID string, relative string) ([]byte, error)
//   - (*Manager).WriteWorkspace(agentID string, relative string, payload []byte) error
//   - (*Manager).DeleteWorkspace(agentID string, relative string) error
//
// </Summary>
package profile

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type WorkspaceEntry struct {
	Name     string    `json:"name"`
	Path     string    `json:"path"`
	Type     string    `json:"type"`
	Size     int64     `json:"size_bytes"`
	Modified time.Time `json:"modified_at"`
}

func (m *Manager) ListWorkspace(agentID string, relative string) ([]WorkspaceEntry, error) {
	path, err := m.workspacePath(agentID, relative, true)
	if err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(path)
	if err != nil {
		return nil, err
	}
	result := make([]WorkspaceEntry, 0, len(entries))
	for _, entry := range entries {
		info, infoErr := entry.Info()
		if infoErr != nil {
			return nil, infoErr
		}
		entryType := "file"
		if entry.IsDir() {
			entryType = "directory"
		}
		entryPath := filepath.ToSlash(filepath.Join(relative, entry.Name()))
		result = append(result, WorkspaceEntry{Name: entry.Name(), Path: strings.TrimPrefix(entryPath, "./"), Type: entryType, Size: info.Size(), Modified: info.ModTime().UTC()})
	}
	sort.Slice(result, func(left int, right int) bool {
		if result[left].Type != result[right].Type {
			return result[left].Type == "directory"
		}
		return result[left].Name < result[right].Name
	})
	return result, nil
}

func (m *Manager) ReadWorkspace(agentID string, relative string) ([]byte, error) {
	path, err := m.workspacePath(agentID, relative, false)
	if err != nil {
		return nil, err
	}
	return os.ReadFile(path)
}

func (m *Manager) WriteWorkspace(agentID string, relative string, payload []byte) error {
	m.barrier.Lock()
	defer m.barrier.Unlock()
	path, err := m.workspacePath(agentID, relative, false)
	if err != nil {
		return err
	}
	return atomicWrite(path, payload, 0o640)
}

func (m *Manager) CreateWorkspaceDirectory(agentID string, relative string) error {
	m.barrier.Lock()
	defer m.barrier.Unlock()
	path, err := m.workspacePath(agentID, relative, false)
	if err != nil {
		return err
	}
	return os.MkdirAll(path, 0o750)
}

func (m *Manager) DeleteWorkspace(agentID string, relative string) error {
	m.barrier.Lock()
	defer m.barrier.Unlock()
	if strings.TrimSpace(relative) == "" || relative == "." {
		return fmt.Errorf("workspace root cannot be deleted")
	}
	path, err := m.workspacePath(agentID, relative, false)
	if err != nil {
		return err
	}
	return os.RemoveAll(path)
}

func (m *Manager) workspacePath(agentID string, relative string, allowRoot bool) (string, error) {
	root, err := m.profileFile(agentID, "workspace")
	if err != nil {
		return "", err
	}
	relative = strings.TrimSpace(relative)
	if relative == "" || relative == "." {
		if allowRoot {
			return root, nil
		}
		return "", fmt.Errorf("path must identify a workspace entry")
	}
	if filepath.IsAbs(relative) {
		return "", fmt.Errorf("workspace path must be relative")
	}
	path := filepath.Clean(filepath.Join(root, relative))
	if !strings.HasPrefix(path, root+string(os.PathSeparator)) {
		return "", fmt.Errorf("workspace path escapes root")
	}
	current := root
	parts := strings.Split(strings.TrimPrefix(path, root+string(os.PathSeparator)), string(os.PathSeparator))
	for index, part := range parts {
		current = filepath.Join(current, part)
		info, statErr := os.Lstat(current)
		if statErr != nil {
			if os.IsNotExist(statErr) {
				break
			}
			return "", statErr
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return "", fmt.Errorf("workspace symlinks are not allowed")
		}
		if index < len(parts)-1 && !info.IsDir() {
			return "", fmt.Errorf("workspace parent is not a directory")
		}
	}
	return path, nil
}

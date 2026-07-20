// <Summary>
// Package profile tests file ownership, persistence, snapshots, and path safety.
// File: Manager_test.go
// Tests:
//   - Per-agent skill writes and snapshots
//   - File-persisted write approval
//   - Workspace traversal rejection
//
// </Summary>
package profile

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSkillWritesStayInsideAgentProfileAndSnapshotEveryVersion(t *testing.T) {
	manager, err := NewManager(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Create("a12345", "Research"); err != nil {
		t.Fatal(err)
	}
	first := "---\nname: research\ndescription: First\n---\n# First\n"
	second := "---\nname: research\ndescription: Second\n---\n# Second\n"
	if _, _, err := manager.WriteSkill("a12345", "research", first); err != nil {
		t.Fatal(err)
	}
	if _, _, err := manager.WriteSkill("a12345", "research", second); err != nil {
		t.Fatal(err)
	}
	profilePath, _ := manager.ProfilePath("a12345")
	payload, err := os.ReadFile(filepath.Join(profilePath, "skills", "research", "SKILL.md"))
	if err != nil {
		t.Fatal(err)
	}
	if string(payload) != second {
		t.Fatalf("profile skill = %q", payload)
	}
	if _, err := os.Stat(filepath.Join(manager.RootProfile(), "skills", "research", "SKILL.md")); !os.IsNotExist(err) {
		t.Fatalf("agent write leaked into root profile: %v", err)
	}
	snapshots, err := manager.ListSnapshots("a12345", "skills")
	if err != nil {
		t.Fatal(err)
	}
	if len(snapshots) != 2 {
		t.Fatalf("snapshots = %d, want 2", len(snapshots))
	}
}

func TestManagerMigratesCategorizedSkillsToCanonicalProfilePath(t *testing.T) {
	root := t.TempDir()
	manager, err := NewManager(root)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Create("m12345", "Migrated"); err != nil {
		t.Fatal(err)
	}
	profilePath, _ := manager.ProfilePath("m12345")
	legacyDir := filepath.Join(profilePath, "skills", "research", "google-news-digest")
	if err := os.MkdirAll(filepath.Join(legacyDir, "references"), 0o750); err != nil {
		t.Fatal(err)
	}
	content := "---\nname: google-news-digest\ndescription: News summary\ncategory: research\n---\n# News\n"
	if err := os.WriteFile(filepath.Join(legacyDir, "SKILL.md"), []byte(content), 0o640); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(legacyDir, "references", "feeds.md"), []byte("feeds"), 0o640); err != nil {
		t.Fatal(err)
	}

	reloaded, err := NewManager(root)
	if err != nil {
		t.Fatal(err)
	}
	canonicalDir := filepath.Join(profilePath, "skills", "google-news-digest")
	payload, err := os.ReadFile(filepath.Join(canonicalDir, "SKILL.md"))
	if err != nil {
		t.Fatal(err)
	}
	if string(payload) != content {
		t.Fatalf("migrated content = %q", payload)
	}
	if _, err := os.Stat(filepath.Join(canonicalDir, "references", "feeds.md")); err != nil {
		t.Fatalf("support file was not migrated: %v", err)
	}
	if _, err := os.Stat(legacyDir); !os.IsNotExist(err) {
		t.Fatalf("legacy skill directory remains: %v", err)
	}
	skills, err := reloaded.ListSkills("m12345")
	if err != nil {
		t.Fatal(err)
	}
	if len(skills) != 1 || skills[0].Path != "skills/google-news-digest" {
		t.Fatalf("skills = %#v", skills)
	}
	snapshots, err := reloaded.ListSnapshots("m12345", "skills")
	if err != nil {
		t.Fatal(err)
	}
	if len(snapshots) != 1 || snapshots[0].Target != "google-news-digest" {
		t.Fatalf("snapshots = %#v", snapshots)
	}
}

func TestSessionApprovalPersistsToAgentConfigFile(t *testing.T) {
	root := t.TempDir()
	manager, err := NewManager(root)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Create("b12345", "Writer"); err != nil {
		t.Fatal(err)
	}
	config, err := manager.PersistApproval("b12345", "memory_write", "session")
	if err != nil {
		t.Fatal(err)
	}
	if config.MemoryWriteApproval {
		t.Fatal("memory approval gate remained enabled")
	}
	profilePath, _ := manager.ProfilePath("b12345")
	payload, err := os.ReadFile(filepath.Join(profilePath, "config.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(payload)
	if !strings.Contains(text, "memory_write: allow") || !strings.Contains(text, "write_approval: false") {
		t.Fatalf("persistent approval missing from config.yaml:\n%s", text)
	}
	reloaded, err := NewManager(root)
	if err != nil {
		t.Fatal(err)
	}
	config, err = reloaded.ReadConfig("b12345")
	if err != nil {
		t.Fatal(err)
	}
	if config.MemoryWriteApproval {
		t.Fatal("persisted approval was lost after manager restart")
	}
	rootConfig, err := reloaded.ReadRootConfig()
	if err != nil {
		t.Fatal(err)
	}
	if !rootConfig.MemoryWriteApproval {
		t.Fatal("agent approval unexpectedly changed root config")
	}
}

func TestWorkspaceRejectsTraversalAndSymlinks(t *testing.T) {
	manager, err := NewManager(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Create("c12345", "Safe"); err != nil {
		t.Fatal(err)
	}
	if err := manager.WriteWorkspace("c12345", "../outside.txt", []byte("bad")); err == nil {
		t.Fatal("traversal was accepted")
	}
	profilePath, _ := manager.ProfilePath("c12345")
	if err := os.Symlink(t.TempDir(), filepath.Join(profilePath, "workspace", "link")); err != nil {
		t.Fatal(err)
	}
	if err := manager.WriteWorkspace("c12345", "link/outside.txt", []byte("bad")); err == nil {
		t.Fatal("symlink escape was accepted")
	}
}

func TestMemoryWritesCreateRestorableSnapshots(t *testing.T) {
	manager, err := NewManager(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Create("d12345", "Memory"); err != nil {
		t.Fatal(err)
	}
	_, first, err := manager.WriteMemory("d12345", "memory", "first", false)
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := manager.WriteMemory("d12345", "memory", "second", false); err != nil {
		t.Fatal(err)
	}
	if _, err := manager.RestoreSnapshot("d12345", first.ID); err != nil {
		t.Fatal(err)
	}
	memory, err := manager.ReadMemory("d12345")
	if err != nil {
		t.Fatal(err)
	}
	if memory["memory"] != "first" {
		t.Fatalf("restored memory = %q", memory["memory"])
	}
}

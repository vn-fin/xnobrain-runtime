// <Summary>
// Portability tests prove lossless profile round trips and reject malicious,
// oversized, undeclared, corrupted, or non-canonical archive payloads.
// File: Portability_test.go
// Tests: round trip, collision remap, cancellation atomicity, archive defenses
// </Summary>
package portability

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/xno/open-lumora/internal/models"
	"github.com/xno/open-lumora/internal/profile"
	"github.com/xno/open-lumora/internal/repositories"
	"github.com/xno/open-lumora/pkg/edition"
	"github.com/xno/open-lumora/pkg/studio/contract"
	"github.com/xno/open-lumora/services/agents"
	"gopkg.in/yaml.v3"
)

type portabilityFixture struct {
	profiles   *profile.Manager
	repository *repositories.File
	agents     *agents.Agents
	service    *Portability
}

type archiveEntry struct {
	name    string
	payload []byte
	mode    os.FileMode
}

func TestPortableBundleRoundTripAndCollisionRemap(t *testing.T) {
	ctx := context.Background()
	source := newPortabilityFixture(t)
	agent, err := source.agents.Create(ctx, "local", "Research Agent", "portable profile")
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := source.profiles.WriteSkill(agent.ID, "research", "---\nname: Research\ndescription: Proof\n---\n\n# Research\n"); err != nil {
		t.Fatal(err)
	}
	if _, _, err := source.profiles.WriteMemory(agent.ID, "memory", "remember this", false); err != nil {
		t.Fatal(err)
	}
	if err := source.profiles.WriteWorkspace(agent.ID, "notes.txt", []byte("portable workspace")); err != nil {
		t.Fatal(err)
	}
	if err := source.profiles.WriteWorkspace(agent.ID, "run.py", []byte("print('quarantine me')\n")); err != nil {
		t.Fatal(err)
	}
	if _, err := source.profiles.PersistApproval(agent.ID, "memory", "allow_all_session"); err != nil {
		t.Fatal(err)
	}
	seedSensitiveConfig(t, source.profiles, agent.ID)
	now := time.Now().UTC()
	job := models.CronJob{ID: "c12345", AgentID: agent.ID, Name: "Daily", Schedule: "@every 1h", Timezone: "Etc/UTC", Mode: "local", MisfirePolicy: "notify", Prompt: "work", Enabled: true, CreatedAt: now, UpdatedAt: now}
	if _, err := source.repository.CreateCron(ctx, "local", job); err != nil {
		t.Fatal(err)
	}

	var bundle bytes.Buffer
	manifest, err := source.service.Export(ctx, "local", ExportOptions{AgentIDs: []string{agent.ID}}, &bundle)
	if err != nil {
		t.Fatal(err)
	}
	if len(manifest.Agents) != 1 || manifest.Agents[0].ID != agent.ID {
		t.Fatalf("unexpected manifest: %+v", manifest)
	}
	if strings.Contains(bundle.String(), "super-secret-token") || strings.Contains(bundle.String(), "https://private.example") || strings.Contains(bundle.String(), source.profiles.DataRoot()) {
		t.Fatal("export leaked provider credentials, endpoint, or source host path")
	}
	inspection, err := source.service.Inspect(bytes.NewReader(bundle.Bytes()), int64(bundle.Len()))
	if err != nil {
		t.Fatal(err)
	}
	if inspection.Files < 6 {
		t.Fatalf("expected profile content, got %d files", inspection.Files)
	}

	target := newPortabilityFixture(t)
	dryRun, err := target.service.DryRun(ctx, "local", bytes.NewReader(bundle.Bytes()), int64(bundle.Len()))
	if err != nil {
		t.Fatal(err)
	}
	if dryRun.PausedCronJobs != 1 || len(dryRun.QuarantinedCode) != 1 || len(dryRun.Collisions) != 0 {
		t.Fatalf("unexpected dry run: %+v", dryRun)
	}
	report, err := target.service.Apply(ctx, "local", bytes.NewReader(bundle.Bytes()), int64(bundle.Len()))
	if err != nil {
		t.Fatal(err)
	}
	importedID := report.AgentIDMappings[agent.ID]
	if importedID != agent.ID || report.PausedCronJobs != 1 || len(report.QuarantinedCode) != 1 {
		t.Fatalf("unexpected import report: %+v", report)
	}
	assertImportedProfile(t, target, importedID)

	second, err := target.service.Apply(ctx, "local", bytes.NewReader(bundle.Bytes()), int64(bundle.Len()))
	if err != nil {
		t.Fatal(err)
	}
	if second.AgentIDMappings[agent.ID] == agent.ID {
		t.Fatal("colliding profile id was not remapped")
	}
	if _, err := target.agents.Get(ctx, "local", second.AgentIDMappings[agent.ID]); err != nil {
		t.Fatalf("remapped profile is not readable: %v", err)
	}
}

func TestPortableBundleCancellationDoesNotPublish(t *testing.T) {
	source := newPortabilityFixture(t)
	agent, err := source.agents.Create(context.Background(), "local", "Canceled", "")
	if err != nil {
		t.Fatal(err)
	}
	var bundle bytes.Buffer
	if _, err := source.service.Export(context.Background(), "local", ExportOptions{AgentIDs: []string{agent.ID}}, &bundle); err != nil {
		t.Fatal(err)
	}
	target := newPortabilityFixture(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := target.service.Apply(ctx, "local", bytes.NewReader(bundle.Bytes()), int64(bundle.Len())); err == nil {
		t.Fatal("canceled import succeeded")
	}
	listed, err := target.agents.List(context.Background(), "local")
	if err != nil {
		t.Fatal(err)
	}
	if len(listed) != 0 {
		t.Fatalf("canceled import published %d profiles", len(listed))
	}
}

func TestPortableBundleRemapsTeamsAndDisablesMissingMembers(t *testing.T) {
	ctx := context.Background()
	source := newPortabilityFixture(t)
	orchestrator, err := source.agents.Create(ctx, "local", "Orchestrator", "")
	if err != nil {
		t.Fatal(err)
	}
	worker, err := source.agents.Create(ctx, "local", "Worker", "")
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	team := models.Team{ID: "team-portable", UserID: "local", Name: "Portable", OrchestratorID: orchestrator.ID, Members: []models.TeamMember{{AgentID: worker.ID, Role: "researcher", AllowedTools: []string{"web"}, Enabled: true}}, MaxParallel: 1, MaxDepth: 1, Enabled: true, CreatedAt: now, UpdatedAt: now}
	if _, err := source.repository.CreateTeam(ctx, team); err != nil {
		t.Fatal(err)
	}
	var partialBundle bytes.Buffer
	if _, err := source.service.Export(ctx, "local", ExportOptions{AgentIDs: []string{orchestrator.ID}}, &partialBundle); err != nil {
		t.Fatal(err)
	}
	target := newPortabilityFixture(t)
	partial, err := target.service.Apply(ctx, "local", bytes.NewReader(partialBundle.Bytes()), int64(partialBundle.Len()))
	if err != nil {
		t.Fatal(err)
	}
	imported, err := target.repository.GetTeam(ctx, "local", partial.TeamIDMappings[team.ID])
	if err != nil {
		t.Fatal(err)
	}
	if imported.Enabled || imported.OrchestratorID != partial.AgentIDMappings[orchestrator.ID] || imported.Members[0].Enabled || !strings.Contains(imported.Members[0].Diagnostic, "not included") || partial.DisabledMembers != 1 {
		t.Fatalf("partial imported team = %#v; report = %#v", imported, partial)
	}

	var fullBundle bytes.Buffer
	if _, err := source.service.Export(ctx, "local", ExportOptions{AgentIDs: []string{orchestrator.ID, worker.ID}}, &fullBundle); err != nil {
		t.Fatal(err)
	}
	fullTarget := newPortabilityFixture(t)
	first, err := fullTarget.service.Apply(ctx, "local", bytes.NewReader(fullBundle.Bytes()), int64(fullBundle.Len()))
	if err != nil {
		t.Fatal(err)
	}
	second, err := fullTarget.service.Apply(ctx, "local", bytes.NewReader(fullBundle.Bytes()), int64(fullBundle.Len()))
	if err != nil {
		t.Fatal(err)
	}
	if first.TeamIDMappings[team.ID] == second.TeamIDMappings[team.ID] {
		t.Fatal("colliding team id was not remapped")
	}
	remapped, err := fullTarget.repository.GetTeam(ctx, "local", second.TeamIDMappings[team.ID])
	if err != nil {
		t.Fatal(err)
	}
	if remapped.OrchestratorID != second.AgentIDMappings[orchestrator.ID] || remapped.Members[0].AgentID != second.AgentIDMappings[worker.ID] || remapped.Members[0].Diagnostic != "" {
		t.Fatalf("team references were not remapped: %#v; report = %#v", remapped, second)
	}
}

func TestPortableBundleArchiveDefenses(t *testing.T) {
	fixture := newPortabilityFixture(t)
	for name, entry := range map[string]archiveEntry{
		"absolute":  {name: "/tmp/evil", payload: []byte("evil")},
		"traversal": {name: "../evil", payload: []byte("evil")},
		"backslash": {name: `profiles\a12345\config.yaml`, payload: []byte("evil")},
		"symlink":   {name: "profiles/a12345/workspace/link", payload: []byte("../outside"), mode: os.ModeSymlink | 0o777},
	} {
		t.Run(name, func(t *testing.T) {
			bundle := rawArchive(t, []archiveEntry{entry})
			if _, err := fixture.service.Inspect(bytes.NewReader(bundle), int64(len(bundle))); err == nil {
				t.Fatalf("accepted unsafe archive entry %q", entry.name)
			}
		})
	}

	t.Run("case folded duplicate", func(t *testing.T) {
		bundle := rawArchive(t, []archiveEntry{{name: "manifest.json", payload: []byte("{}")}, {name: "MANIFEST.JSON", payload: []byte("{}")}})
		if _, err := fixture.service.Inspect(bytes.NewReader(bundle), int64(len(bundle))); err == nil || !strings.Contains(err.Error(), "duplicate") {
			t.Fatalf("expected duplicate error, got %v", err)
		}
	})

	for name, mutate := range map[string]func(*Manifest, map[string]Checksum, *[]archiveEntry){
		"unsupported version": func(manifest *Manifest, _ map[string]Checksum, _ *[]archiveEntry) { manifest.Version = 99 },
		"unsupported capability": func(manifest *Manifest, _ map[string]Checksum, _ *[]archiveEntry) {
			manifest.RequiredCapabilities = []string{"future-runtime"}
		},
		"undeclared profile": func(_ *Manifest, _ map[string]Checksum, entries *[]archiveEntry) {
			*entries = append(*entries, archiveEntry{name: "profiles/b12345/config.yaml", payload: []byte("model: {}\n")})
		},
		"compatibility link traversal": func(_ *Manifest, _ map[string]Checksum, entries *[]archiveEntry) {
			*entries = append(*entries, archiveEntry{name: "profiles/a12345/home/.hermes/escape", payload: []byte("evil")})
		},
		"checksum mismatch": func(_ *Manifest, checksums map[string]Checksum, _ *[]archiveEntry) {
			checksums["profiles/a12345/config.yaml"] = Checksum{SHA256: strings.Repeat("0", 64), Size: 10}
		},
	} {
		t.Run(name, func(t *testing.T) {
			bundle := portableArchive(t, mutate)
			if _, err := fixture.service.Inspect(bytes.NewReader(bundle), int64(len(bundle))); err == nil {
				t.Fatalf("accepted invalid bundle: %s", name)
			}
		})
	}
}

func TestPortableBundleResourceLimits(t *testing.T) {
	fixture := newPortabilityFixture(t)
	bundle := portableArchive(t, nil)

	fixture.service.limits.MaxCompressedBytes = int64(len(bundle) - 1)
	if _, err := fixture.service.Inspect(bytes.NewReader(bundle), int64(len(bundle))); err == nil || !strings.Contains(err.Error(), "compressed size") {
		t.Fatalf("expected compressed limit error, got %v", err)
	}
	fixture.service.limits.MaxCompressedBytes = int64(len(bundle) + 1)
	fixture.service.limits.MaxFiles = 2
	if _, err := fixture.service.Inspect(bytes.NewReader(bundle), int64(len(bundle))); err == nil || !strings.Contains(err.Error(), "file count") {
		t.Fatalf("expected file count error, got %v", err)
	}
	fixture.service.limits.MaxFiles = 10
	fixture.service.limits.MaxFileBytes = 8
	if _, err := fixture.service.Inspect(bytes.NewReader(bundle), int64(len(bundle))); err == nil || !strings.Contains(err.Error(), "file exceeds") {
		t.Fatalf("expected per-file limit error, got %v", err)
	}
}

func TestStandaloneInspectorUsesPortableContractWithoutProfileState(t *testing.T) {
	bundle := portableArchive(t, nil)
	inspection, err := NewInspector().Inspect(bytes.NewReader(bundle), int64(len(bundle)))
	if err != nil || len(inspection.Manifest.Agents) != 1 {
		t.Fatalf("standalone inspection = %#v, %v", inspection, err)
	}
	unsafe := rawArchive(t, []archiveEntry{{name: "../escape", payload: []byte("bad")}})
	if _, err := NewInspector().Inspect(bytes.NewReader(unsafe), int64(len(unsafe))); err == nil {
		t.Fatal("standalone inspector accepted traversal")
	}
}

func TestManagedStageCommitIsRestartSafeAndRollbackRemovesVisibility(t *testing.T) {
	ctx := context.Background()
	source := newPortabilityFixture(t)
	agent, err := source.agents.Create(ctx, "local", "Staged agent", "")
	if err != nil {
		t.Fatal(err)
	}
	var bundle bytes.Buffer
	if _, err := source.service.Export(ctx, "local", ExportOptions{AgentIDs: []string{agent.ID}}, &bundle); err != nil {
		t.Fatal(err)
	}
	target := newPortabilityFixture(t)
	stage, err := target.service.StageManaged(ctx, "local", "import-123", bytes.NewReader(bundle.Bytes()), int64(bundle.Len()))
	if err != nil {
		t.Fatal(err)
	}
	importedID := stage.Report.AgentIDMappings[agent.ID]
	if _, err := target.agents.Get(ctx, "local", importedID); !errors.Is(err, repositories.ErrNotFound) {
		t.Fatalf("staged agent was visible before commit: %v", err)
	}
	restarted := NewPortability(target.agents, target.profiles, target.repository)
	committed, err := restarted.CommitManaged(ctx, "local", "import-123")
	if err != nil || committed.State != "committed" {
		t.Fatalf("commit = %#v, %v", committed, err)
	}
	if _, err := target.agents.Get(ctx, "local", importedID); err != nil {
		t.Fatalf("committed agent is not visible: %v", err)
	}
	if second, err := restarted.CommitManaged(ctx, "local", "import-123"); err != nil || second.Report.AgentIDMappings[agent.ID] != importedID {
		t.Fatalf("idempotent commit = %#v, %v", second, err)
	}
	if err := restarted.RollbackManaged(ctx, "local", "import-123"); err != nil {
		t.Fatal(err)
	}
	if _, err := target.agents.Get(ctx, "local", importedID); !errors.Is(err, repositories.ErrNotFound) {
		t.Fatalf("rolled back agent remains visible: %v", err)
	}
	if err := restarted.RollbackManaged(ctx, "local", "import-123"); err != nil {
		t.Fatalf("idempotent rollback: %v", err)
	}
}

func newPortabilityFixture(t *testing.T) portabilityFixture {
	t.Helper()
	profiles, err := profile.NewManager(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	repository, err := repositories.NewFile(profiles)
	if err != nil {
		t.Fatal(err)
	}
	agentService := agents.NewAgents(repository, profiles, edition.OpenSource{AgentLimit: 100, CronJobLimit: 100, CronConcurrency: 1, CronRunsPerDay: 10, CronRunsPerMonth: 200})
	return portabilityFixture{profiles: profiles, repository: repository, agents: agentService, service: NewPortability(agentService, profiles, repository)}
}

func seedSensitiveConfig(t *testing.T, profiles *profile.Manager, agentID string) {
	t.Helper()
	if err := profiles.MutateProfile(agentID, func(root string) error {
		path := filepath.Join(root, "config.yaml")
		payload, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		var config contract.ProfileConfig
		if err := yaml.Unmarshal(payload, &config); err != nil {
			return err
		}
		config.Model.Provider = "private"
		config.Model.BaseURL = "https://private.example/super-secret-token"
		config.Providers = map[string]contract.ProviderConfig{"private": {Name: "super-secret-token", API: "https://private.example"}}
		encoded, err := yaml.Marshal(config)
		if err != nil {
			return err
		}
		return os.WriteFile(path, encoded, 0o640)
	}); err != nil {
		t.Fatal(err)
	}
}

func assertImportedProfile(t *testing.T, fixture portabilityFixture, agentID string) {
	t.Helper()
	memory, err := fixture.profiles.ReadMemory(agentID)
	if err != nil || strings.TrimSpace(memory["memory"]) != "remember this" {
		t.Fatalf("memory did not round trip: %q, %v", memory["memory"], err)
	}
	skill, err := os.ReadFile(filepath.Join(fixture.profiles.DataRoot(), "profiles", agentID, "skills", "research", "SKILL.md"))
	if err != nil || !strings.Contains(string(skill), "# Research") {
		t.Fatalf("skill did not round trip: %v", err)
	}
	workspace, err := fixture.profiles.ReadWorkspace(agentID, "notes.txt")
	if err != nil || string(workspace) != "portable workspace" {
		t.Fatalf("workspace did not round trip: %q, %v", workspace, err)
	}
	if _, err := fixture.profiles.ReadWorkspace(agentID, "run.py"); !os.IsNotExist(err) {
		t.Fatalf("custom code was not quarantined: %v", err)
	}
	if payload, err := os.ReadFile(filepath.Join(fixture.profiles.DataRoot(), "profiles", agentID, "quarantine", "workspace", "run.py")); err != nil || !bytes.Contains(payload, []byte("quarantine me")) {
		t.Fatalf("quarantined code missing: %v", err)
	}
	jobs, err := fixture.repository.ListCrons(context.Background(), "local")
	if err != nil || len(jobs) != 1 || jobs[0].Enabled || jobs[0].AgentID != agentID {
		t.Fatalf("imported cron is not paused: %+v, %v", jobs, err)
	}
	configPayload, err := os.ReadFile(filepath.Join(fixture.profiles.DataRoot(), "profiles", agentID, "config.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	var config contract.ProfileConfig
	if err := yaml.Unmarshal(configPayload, &config); err != nil {
		t.Fatal(err)
	}
	if len(config.Providers) != 0 || config.Model.Provider != "" || config.Model.BaseURL != "" || len(config.Approvals.Persisted) != 0 || !config.Memory.WriteApproval || !config.Skills.WriteApproval {
		t.Fatalf("unsafe configuration survived import: %+v", config)
	}
	if !strings.HasPrefix(config.Terminal.CWD, fixture.profiles.DataRoot()) {
		t.Fatalf("terminal cwd was not rewritten: %s", config.Terminal.CWD)
	}
}

func portableArchive(t *testing.T, mutate func(*Manifest, map[string]Checksum, *[]archiveEntry)) []byte {
	t.Helper()
	manifest := Manifest{Format: bundleFormat, Version: bundleVersion, SourceVersion: appVersion, CreatedAt: time.Unix(1, 0).UTC(), ExportID: "e12345", Agents: []ManifestAgent{{ID: "a12345", Name: "Agent", Title: "Agent", CreatedAt: time.Unix(1, 0).UTC(), UpdatedAt: time.Unix(1, 0).UTC()}}}
	entries := []archiveEntry{{name: "profiles/a12345/config.yaml", payload: []byte("model:\n  default: auto\napprovals:\n  mode: manual\nskills:\n  write_approval: true\nmemory:\n  write_approval: true\nterminal:\n  backend: local\n  cwd: ''\n")}}
	checksums := make(map[string]Checksum)
	if mutate != nil {
		mutate(&manifest, checksums, &entries)
	}
	manifestPayload, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	all := append([]archiveEntry{{name: "manifest.json", payload: manifestPayload}}, entries...)
	for _, entry := range all {
		if _, exists := checksums[entry.name]; exists {
			continue
		}
		hash := sha256.Sum256(entry.payload)
		checksums[entry.name] = Checksum{SHA256: hex.EncodeToString(hash[:]), Size: int64(len(entry.payload))}
	}
	checksumPayload, err := json.Marshal(checksums)
	if err != nil {
		t.Fatal(err)
	}
	all = append(all, archiveEntry{name: "checksums.json", payload: checksumPayload})
	return rawArchive(t, all)
}

func rawArchive(t *testing.T, entries []archiveEntry) []byte {
	t.Helper()
	var output bytes.Buffer
	archive := zip.NewWriter(&output)
	for _, entry := range entries {
		header := &zip.FileHeader{Name: entry.name, Method: zip.Deflate}
		if entry.mode != 0 {
			header.SetMode(entry.mode)
		}
		writer, err := archive.CreateHeader(header)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := writer.Write(entry.payload); err != nil {
			t.Fatal(err)
		}
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	return output.Bytes()
}

// <Summary>
// Package portability exports, validates, previews, and atomically imports the
// versioned .lumora profile bundle without including credentials or host state.
// File: Portability.go
// Types: Portability, Manifest, Inspection, ImportReport
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
	"fmt"
	"io"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode"

	"github.com/xno/open-lumora/internal/identity"
	"github.com/xno/open-lumora/internal/profile"
	"github.com/xno/open-lumora/internal/repositories"
	"github.com/xno/open-lumora/internal/studio/contract"
	"github.com/xno/open-lumora/services/agents"
	"gopkg.in/yaml.v3"
)

const (
	bundleFormat  = "open-lumora-bundle"
	bundleVersion = 1
	appVersion    = "0.1.0"
)

type Limits struct {
	MaxCompressedBytes  int64
	MaxExpandedBytes    int64
	MaxFileBytes        int64
	MaxFiles            int
	MaxPathDepth        int
	MaxCompressionRatio uint64
}

type Manifest struct {
	Format               string          `json:"format"`
	Version              int             `json:"version"`
	SourceVersion        string          `json:"source_version"`
	CreatedAt            time.Time       `json:"created_at"`
	ExportID             string          `json:"export_id"`
	Agents               []ManifestAgent `json:"agents"`
	Teams                []ManifestTeam  `json:"teams,omitempty"`
	Included             []string        `json:"included"`
	RequiredCapabilities []string        `json:"required_capabilities"`
}

type ManifestTeam struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

type ManifestAgent struct {
	ID          string    `json:"id"`
	Name        string    `json:"name"`
	Title       string    `json:"title"`
	Description string    `json:"description"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
}

type Checksum struct {
	SHA256 string `json:"sha256"`
	Size   int64  `json:"size"`
}

type Inspection struct {
	Manifest      Manifest `json:"manifest"`
	Files         int      `json:"files"`
	ExpandedBytes int64    `json:"expanded_bytes"`
	Warnings      []string `json:"warnings"`
}

type DryRun struct {
	Inspection      Inspection `json:"inspection"`
	Collisions      []string   `json:"collisions"`
	ApprovalResets  int        `json:"approval_resets"`
	PausedCronJobs  int        `json:"paused_cron_jobs"`
	ProvidersReset  int        `json:"providers_reset"`
	QuarantinedCode []string   `json:"quarantined_code"`
	StorageRequired int64      `json:"storage_required"`
	TeamCollisions  []string   `json:"team_collisions,omitempty"`
}

type ImportReport struct {
	ExportID        string            `json:"export_id"`
	AgentIDMappings map[string]string `json:"agent_id_mappings"`
	TeamIDMappings  map[string]string `json:"team_id_mappings,omitempty"`
	DisabledMembers int               `json:"disabled_team_members"`
	PausedCronJobs  int               `json:"paused_cron_jobs"`
	ApprovalResets  int               `json:"approval_resets"`
	ProvidersReset  int               `json:"providers_reset"`
	QuarantinedCode []string          `json:"quarantined_code"`
	Warnings        []string          `json:"warnings"`
}

type ExportOptions struct {
	AgentIDs             []string
	IncludeConversations bool
}

type Portability struct {
	agents    *agents.Agents
	profiles  *profile.Manager
	teams     contract.TeamStore
	limits    Limits
	now       func() time.Time
	stagingMu sync.Mutex
}

type ManagedStage struct {
	StageID string       `json:"stage_id"`
	State   string       `json:"state"`
	Report  ImportReport `json:"report"`
}

type managedStageRecord struct {
	StageID string          `json:"stage_id"`
	UserID  string          `json:"user_id"`
	State   string          `json:"state"`
	Report  ImportReport    `json:"report"`
	Teams   []contract.Team `json:"teams,omitempty"`
}

type Inspector struct{ service *Portability }

var (
	portableIDPattern     = regexp.MustCompile(`^[a-z][a-z0-9]{5}$`)
	managedStageIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`)
)

func NewInspector() *Inspector {
	return &Inspector{service: &Portability{limits: defaultLimits()}}
}

func (i *Inspector) Inspect(reader io.ReaderAt, size int64) (Inspection, error) {
	return i.service.Inspect(reader, size)
}

func NewPortability(agentService *agents.Agents, profiles *profile.Manager, teamStores ...contract.TeamStore) *Portability {
	service := &Portability{agents: agentService, profiles: profiles, limits: defaultLimits(), now: func() time.Time { return time.Now().UTC() }}
	if len(teamStores) > 0 {
		service.teams = teamStores[0]
	}
	return service
}

func (s *Portability) Export(ctx context.Context, userID string, options ExportOptions, output io.Writer) (Manifest, error) {
	if output == nil {
		return Manifest{}, fmt.Errorf("bundle output is required")
	}
	if len(options.AgentIDs) == 0 {
		return Manifest{}, fmt.Errorf("at least one agent is required")
	}
	exportID, err := identity.NewID()
	if err != nil {
		return Manifest{}, err
	}
	manifest := Manifest{Format: bundleFormat, Version: bundleVersion, SourceVersion: appVersion, CreatedAt: s.now(), ExportID: exportID, Included: []string{"config", "memory", "skills", "workspace", "snapshots", "crons"}, RequiredCapabilities: []string{}}
	if options.IncludeConversations {
		manifest.Included = append(manifest.Included, "conversations")
	}
	for _, agentID := range uniqueStrings(options.AgentIDs) {
		agent, err := s.agents.Get(ctx, userID, agentID)
		if err != nil {
			return Manifest{}, err
		}
		manifest.Agents = append(manifest.Agents, ManifestAgent{ID: agent.ID, Name: agent.Name, Title: agent.Title, Description: agent.Description, CreatedAt: agent.CreatedAt, UpdatedAt: agent.UpdatedAt})
	}
	sort.Slice(manifest.Agents, func(left, right int) bool { return manifest.Agents[left].ID < manifest.Agents[right].ID })
	selectedAgents := make(map[string]bool, len(manifest.Agents))
	for _, agent := range manifest.Agents {
		selectedAgents[agent.ID] = true
	}
	var exportedTeams []contract.Team
	if s.teams != nil {
		teams, err := s.teams.ListTeams(ctx, userID)
		if err != nil {
			return Manifest{}, err
		}
		for _, team := range teams {
			if selectedAgents[team.OrchestratorID] {
				exportedTeams = append(exportedTeams, team)
				manifest.Teams = append(manifest.Teams, ManifestTeam{ID: team.ID, Name: team.Name})
			}
		}
		sort.Slice(manifest.Teams, func(left, right int) bool { return manifest.Teams[left].ID < manifest.Teams[right].ID })
		if len(manifest.Teams) > 0 {
			manifest.Included = append(manifest.Included, "teams")
		}
	}
	archive := zip.NewWriter(output)
	checksums := make(map[string]Checksum)
	manifestPayload, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return Manifest{}, err
	}
	manifestPayload = append(manifestPayload, '\n')
	if err := writeBundleBytes(archive, "manifest.json", manifestPayload, checksums); err != nil {
		return Manifest{}, err
	}
	for _, agent := range manifest.Agents {
		if err := ctx.Err(); err != nil {
			return Manifest{}, err
		}
		err := s.profiles.SnapshotProfile(agent.ID, func(root string) error {
			return s.exportProfile(ctx, archive, checksums, agent.ID, root, options.IncludeConversations)
		})
		if err != nil {
			return Manifest{}, err
		}
	}
	for _, team := range exportedTeams {
		payload, err := yaml.Marshal(team)
		if err != nil {
			return Manifest{}, err
		}
		if err := writeBundleBytes(archive, path.Join("teams", team.ID+".yaml"), payload, checksums); err != nil {
			return Manifest{}, err
		}
	}
	checksumPayload, err := json.MarshalIndent(checksums, "", "  ")
	if err != nil {
		return Manifest{}, err
	}
	checksumPayload = append(checksumPayload, '\n')
	if err := writeBundleBytes(archive, "checksums.json", checksumPayload, nil); err != nil {
		return Manifest{}, err
	}
	if err := archive.Close(); err != nil {
		return Manifest{}, err
	}
	return manifest, nil
}

func (s *Portability) Inspect(reader io.ReaderAt, size int64) (Inspection, error) {
	archive, err := s.openArchive(reader, size)
	if err != nil {
		return Inspection{}, err
	}
	files := make(map[string]*zip.File)
	caseFolded := make(map[string]string)
	var expanded int64
	for _, file := range archive.File {
		name, err := safeArchivePath(file.Name, s.limits.MaxPathDepth)
		if err != nil {
			return Inspection{}, err
		}
		folded := strings.ToLower(name)
		if previous, duplicate := caseFolded[folded]; duplicate {
			return Inspection{}, fmt.Errorf("duplicate archive path %q conflicts with %q", name, previous)
		}
		caseFolded[folded] = name
		if file.FileInfo().Mode()&os.ModeSymlink != 0 {
			return Inspection{}, fmt.Errorf("archive symlink is forbidden: %s", name)
		}
		if file.FileInfo().IsDir() {
			continue
		}
		if len(files)+1 > s.limits.MaxFiles {
			return Inspection{}, fmt.Errorf("archive file count exceeds limit")
		}
		if file.UncompressedSize64 > uint64(s.limits.MaxFileBytes) {
			return Inspection{}, fmt.Errorf("archive file exceeds size limit: %s", name)
		}
		compressed := file.CompressedSize64
		if compressed == 0 {
			compressed = 1
		}
		if file.UncompressedSize64/compressed > s.limits.MaxCompressionRatio {
			return Inspection{}, fmt.Errorf("archive compression ratio exceeds limit: %s", name)
		}
		expanded += int64(file.UncompressedSize64)
		if expanded > s.limits.MaxExpandedBytes {
			return Inspection{}, fmt.Errorf("archive expanded size exceeds limit")
		}
		files[name] = file
	}
	manifestFile, ok := files["manifest.json"]
	if !ok {
		return Inspection{}, fmt.Errorf("manifest.json is required")
	}
	checksumsFile, ok := files["checksums.json"]
	if !ok {
		return Inspection{}, fmt.Errorf("checksums.json is required")
	}
	var manifest Manifest
	if err := decodeZipJSON(manifestFile, &manifest, s.limits.MaxFileBytes); err != nil {
		return Inspection{}, err
	}
	if manifest.Format != bundleFormat || manifest.Version != bundleVersion {
		return Inspection{}, fmt.Errorf("unsupported bundle format or version")
	}
	if len(manifest.RequiredCapabilities) > 0 {
		return Inspection{}, fmt.Errorf("bundle requires unsupported capabilities: %s", strings.Join(manifest.RequiredCapabilities, ","))
	}
	manifestAgents := make(map[string]bool, len(manifest.Agents))
	for _, agent := range manifest.Agents {
		if !portableIDPattern.MatchString(agent.ID) {
			return Inspection{}, fmt.Errorf("invalid manifest agent id %q", agent.ID)
		}
		if manifestAgents[agent.ID] {
			return Inspection{}, fmt.Errorf("duplicate manifest agent id %q", agent.ID)
		}
		manifestAgents[agent.ID] = true
	}
	manifestTeams := make(map[string]bool, len(manifest.Teams))
	for _, team := range manifest.Teams {
		if team.ID == "" || strings.ContainsAny(team.ID, "/\\\x00") || manifestTeams[team.ID] {
			return Inspection{}, fmt.Errorf("invalid or duplicate manifest team id %q", team.ID)
		}
		manifestTeams[team.ID] = true
	}
	for name := range files {
		if strings.HasPrefix(name, "teams/") {
			parts := strings.Split(name, "/")
			if len(parts) != 2 || path.Ext(parts[1]) != ".yaml" || !manifestTeams[strings.TrimSuffix(parts[1], ".yaml")] {
				return Inspection{}, fmt.Errorf("payload references undeclared team: %s", name)
			}
			continue
		}
		if !strings.HasPrefix(name, "profiles/") {
			continue
		}
		parts := strings.Split(name, "/")
		if len(parts) < 3 || !manifestAgents[parts[1]] {
			return Inspection{}, fmt.Errorf("payload references undeclared agent: %s", name)
		}
		if !allowedProfilePath(strings.Join(parts[2:], "/")) {
			return Inspection{}, fmt.Errorf("profile payload path is not portable: %s", name)
		}
	}
	var checksums map[string]Checksum
	if err := decodeZipJSON(checksumsFile, &checksums, s.limits.MaxFileBytes); err != nil {
		return Inspection{}, err
	}
	for name, file := range files {
		if name == "checksums.json" {
			continue
		}
		expected, listed := checksums[name]
		if !listed {
			return Inspection{}, fmt.Errorf("archive payload is not checksummed: %s", name)
		}
		actual, err := hashZipFile(file, s.limits.MaxFileBytes)
		if err != nil {
			return Inspection{}, err
		}
		if actual.SHA256 != expected.SHA256 || actual.Size != expected.Size {
			return Inspection{}, fmt.Errorf("checksum mismatch: %s", name)
		}
		delete(checksums, name)
	}
	if len(checksums) != 0 {
		return Inspection{}, fmt.Errorf("checksums list missing archive payloads")
	}
	for _, agent := range manifest.Agents {
		if _, ok := files[path.Join("profiles", agent.ID, "config.yaml")]; !ok {
			return Inspection{}, fmt.Errorf("profile %s has no config.yaml", agent.ID)
		}
	}
	for _, team := range manifest.Teams {
		if _, ok := files[path.Join("teams", team.ID+".yaml")]; !ok {
			return Inspection{}, fmt.Errorf("team %s has no definition", team.ID)
		}
	}
	return Inspection{Manifest: manifest, Files: len(files), ExpandedBytes: expanded, Warnings: []string{}}, nil
}

func defaultLimits() Limits {
	return Limits{MaxCompressedBytes: 2 << 30, MaxExpandedBytes: 4 << 30, MaxFileBytes: 256 << 20, MaxFiles: 100000, MaxPathDepth: 32, MaxCompressionRatio: 200}
}

func (s *Portability) DryRun(ctx context.Context, userID string, reader io.ReaderAt, size int64) (DryRun, error) {
	inspection, err := s.Inspect(reader, size)
	if err != nil {
		return DryRun{}, err
	}
	report := DryRun{Inspection: inspection, ApprovalResets: len(inspection.Manifest.Agents), ProvidersReset: len(inspection.Manifest.Agents), StorageRequired: inspection.ExpandedBytes}
	for _, agent := range inspection.Manifest.Agents {
		if _, err := s.agents.Get(ctx, userID, agent.ID); err == nil {
			report.Collisions = append(report.Collisions, agent.ID)
		} else if !errors.Is(err, repositories.ErrNotFound) {
			return DryRun{}, err
		}
	}
	if s.teams != nil {
		for _, team := range inspection.Manifest.Teams {
			if _, err := s.teams.GetTeam(ctx, userID, team.ID); err == nil {
				report.TeamCollisions = append(report.TeamCollisions, team.ID)
			} else if !errors.Is(err, repositories.ErrNotFound) {
				return DryRun{}, err
			}
		}
	}
	archive, _ := s.openArchive(reader, size)
	for _, file := range archive.File {
		if file.FileInfo().IsDir() {
			continue
		}
		if strings.Contains(file.Name, "/cron/jobs/") && strings.HasSuffix(file.Name, ".yaml") {
			report.PausedCronJobs++
		}
		if isCustomCode(file.Name) {
			report.QuarantinedCode = append(report.QuarantinedCode, file.Name)
		}
	}
	sort.Strings(report.Collisions)
	sort.Strings(report.QuarantinedCode)
	sort.Strings(report.TeamCollisions)
	return report, nil
}

func (s *Portability) Apply(ctx context.Context, userID string, reader io.ReaderAt, size int64) (ImportReport, error) {
	return s.apply(ctx, userID, reader, size, "")
}

func (s *Portability) StageManaged(ctx context.Context, userID string, stageID string, reader io.ReaderAt, size int64) (ManagedStage, error) {
	if !managedStageIDPattern.MatchString(stageID) {
		return ManagedStage{}, fmt.Errorf("invalid managed import stage id")
	}
	s.stagingMu.Lock()
	defer s.stagingMu.Unlock()
	if record, err := s.readManagedStage(stageID); err == nil {
		if record.UserID != userID {
			return ManagedStage{}, fmt.Errorf("managed import stage owner mismatch")
		}
		return ManagedStage{StageID: stageID, State: record.State, Report: record.Report}, nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return ManagedStage{}, err
	}
	report, err := s.apply(ctx, userID, reader, size, stageID)
	if err != nil {
		return ManagedStage{}, err
	}
	return ManagedStage{StageID: stageID, State: "staged", Report: report}, nil
}

func (s *Portability) CommitManaged(ctx context.Context, userID string, stageID string) (ManagedStage, error) {
	if !managedStageIDPattern.MatchString(stageID) {
		return ManagedStage{}, fmt.Errorf("invalid managed import stage id")
	}
	s.stagingMu.Lock()
	defer s.stagingMu.Unlock()
	record, err := s.readManagedStage(stageID)
	if err != nil {
		return ManagedStage{}, err
	}
	if record.UserID != userID {
		return ManagedStage{}, fmt.Errorf("managed import stage owner mismatch")
	}
	if record.State == "committed" {
		return ManagedStage{StageID: stageID, State: record.State, Report: record.Report}, nil
	}
	if record.State != "staged" {
		return ManagedStage{}, fmt.Errorf("managed import stage is not committable")
	}
	root := s.managedStageRoot(stageID)
	staged := make(map[string]string, len(record.Report.AgentIDMappings))
	for _, newID := range record.Report.AgentIDMappings {
		staged[newID] = filepath.Join(root, newID)
	}
	if err := s.profiles.PublishStagedProfiles(staged); err != nil {
		return ManagedStage{}, err
	}
	createdTeams := make([]string, 0, len(record.Teams))
	for _, team := range record.Teams {
		if s.teams == nil {
			break
		}
		if _, err := s.teams.CreateTeam(ctx, team); err != nil {
			for index := len(createdTeams) - 1; index >= 0; index-- {
				_ = s.teams.DeleteTeam(context.Background(), userID, createdTeams[index])
			}
			_ = s.profiles.UnpublishProfiles(staged)
			return ManagedStage{}, fmt.Errorf("publish imported team %s: %w", team.ID, err)
		}
		createdTeams = append(createdTeams, team.ID)
	}
	record.State = "committed"
	if err := s.writeManagedStage(root, record); err != nil {
		for index := len(createdTeams) - 1; index >= 0; index-- {
			_ = s.teams.DeleteTeam(context.Background(), userID, createdTeams[index])
		}
		_ = s.profiles.UnpublishProfiles(staged)
		return ManagedStage{}, err
	}
	return ManagedStage{StageID: stageID, State: record.State, Report: record.Report}, nil
}

func (s *Portability) RollbackManaged(ctx context.Context, userID string, stageID string) error {
	if !managedStageIDPattern.MatchString(stageID) {
		return fmt.Errorf("invalid managed import stage id")
	}
	s.stagingMu.Lock()
	defer s.stagingMu.Unlock()
	record, err := s.readManagedStage(stageID)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if record.UserID != userID {
		return fmt.Errorf("managed import stage owner mismatch")
	}
	root := s.managedStageRoot(stageID)
	if record.State == "committed" {
		for _, team := range record.Teams {
			if s.teams != nil {
				if err := s.teams.DeleteTeam(ctx, userID, team.ID); err != nil && !errors.Is(err, repositories.ErrNotFound) {
					return err
				}
			}
		}
		staged := make(map[string]string, len(record.Report.AgentIDMappings))
		for _, newID := range record.Report.AgentIDMappings {
			staged[newID] = filepath.Join(root, newID)
		}
		if err := s.profiles.UnpublishProfiles(staged); err != nil {
			return err
		}
	}
	return os.RemoveAll(root)
}

func (s *Portability) apply(ctx context.Context, userID string, reader io.ReaderAt, size int64, managedStageID string) (ImportReport, error) {
	dryRun, err := s.DryRun(ctx, userID, reader, size)
	if err != nil {
		return ImportReport{}, err
	}
	archive, err := s.openArchive(reader, size)
	if err != nil {
		return ImportReport{}, err
	}
	report := ImportReport{ExportID: dryRun.Inspection.Manifest.ExportID, AgentIDMappings: make(map[string]string), TeamIDMappings: make(map[string]string), ApprovalResets: dryRun.ApprovalResets, ProvidersReset: dryRun.ProvidersReset, Warnings: dryRun.Inspection.Warnings}
	for _, agent := range dryRun.Inspection.Manifest.Agents {
		newID := agent.ID
		if contains(dryRun.Collisions, agent.ID) {
			newID, err = identity.NewID()
			if err != nil {
				return ImportReport{}, err
			}
		}
		report.AgentIDMappings[agent.ID] = newID
	}
	for _, team := range dryRun.Inspection.Manifest.Teams {
		newID := team.ID
		if contains(dryRun.TeamCollisions, team.ID) {
			newID, err = identity.NewID()
			if err != nil {
				return ImportReport{}, err
			}
		}
		report.TeamIDMappings[team.ID] = newID
	}
	importsRoot := filepath.Join(s.profiles.DataRoot(), "imports")
	if err := os.MkdirAll(importsRoot, 0o750); err != nil {
		return ImportReport{}, err
	}
	stagingParent := importsRoot
	if managedStageID != "" {
		stagingParent = filepath.Join(importsRoot, "managed")
		if err := os.MkdirAll(stagingParent, 0o750); err != nil {
			return ImportReport{}, err
		}
	}
	stagingRoot, err := os.MkdirTemp(stagingParent, ".staging-")
	if err != nil {
		return ImportReport{}, err
	}
	cleanupStaging := true
	defer func() {
		if cleanupStaging {
			_ = os.RemoveAll(stagingRoot)
		}
	}()
	manifestAgents := make(map[string]ManifestAgent)
	for _, agent := range dryRun.Inspection.Manifest.Agents {
		manifestAgents[agent.ID] = agent
		newID := report.AgentIDMappings[agent.ID]
		for _, directory := range []string{"skills", "memories", "workspace", "sessions", "cron/jobs", "logs", "snapshots", "home", "quarantine"} {
			if err := os.MkdirAll(filepath.Join(stagingRoot, newID, filepath.FromSlash(directory)), 0o750); err != nil {
				return ImportReport{}, err
			}
		}
		if err := os.Symlink("..", filepath.Join(stagingRoot, newID, "home", ".hermes")); err != nil {
			return ImportReport{}, err
		}
	}
	stagedTeams := make([]contract.Team, 0, len(dryRun.Inspection.Manifest.Teams))
	for _, file := range archive.File {
		if err := ctx.Err(); err != nil {
			return ImportReport{}, err
		}
		name, err := safeArchivePath(file.Name, s.limits.MaxPathDepth)
		if err != nil || file.FileInfo().IsDir() {
			continue
		}
		if strings.HasPrefix(name, "teams/") {
			payload, readErr := readZipBytes(file, s.limits.MaxFileBytes)
			if readErr != nil {
				return ImportReport{}, readErr
			}
			var team contract.Team
			if err := yaml.Unmarshal(payload, &team); err != nil {
				return ImportReport{}, fmt.Errorf("decode team %s: %w", name, err)
			}
			mappedOrchestrator, exists := report.AgentIDMappings[team.OrchestratorID]
			if !exists {
				return ImportReport{}, fmt.Errorf("team %s orchestrator is not included", team.ID)
			}
			team.ID = report.TeamIDMappings[team.ID]
			team.UserID = userID
			team.OrchestratorID = mappedOrchestrator
			team.Enabled = false
			team.UpdatedAt = s.now()
			for index := range team.Members {
				mappedMember, included := report.AgentIDMappings[team.Members[index].AgentID]
				if !included {
					team.Members[index].Enabled = false
					team.Members[index].Diagnostic = "agent was not included in this bundle"
					report.DisabledMembers++
					continue
				}
				team.Members[index].AgentID = mappedMember
				team.Members[index].Diagnostic = ""
			}
			stagedTeams = append(stagedTeams, team)
			continue
		}
		if !strings.HasPrefix(name, "profiles/") {
			continue
		}
		parts := strings.Split(name, "/")
		if len(parts) < 3 {
			return ImportReport{}, fmt.Errorf("invalid profile payload path")
		}
		oldID := parts[1]
		newID, exists := report.AgentIDMappings[oldID]
		if !exists {
			return ImportReport{}, fmt.Errorf("payload references unknown agent %s", oldID)
		}
		relative := strings.Join(parts[2:], "/")
		destinationRelative := relative
		if isCustomCode(name) {
			destinationRelative = path.Join("quarantine", relative)
			report.QuarantinedCode = append(report.QuarantinedCode, name)
		}
		destination := filepath.Join(stagingRoot, newID, filepath.FromSlash(destinationRelative))
		if err := os.MkdirAll(filepath.Dir(destination), 0o750); err != nil {
			return ImportReport{}, err
		}
		payload, err := readZipBytes(file, s.limits.MaxFileBytes)
		if err != nil {
			return ImportReport{}, err
		}
		payload, paused, err := s.transformImportPayload(relative, payload, oldID, newID, userID, manifestAgents[oldID])
		if err != nil {
			return ImportReport{}, err
		}
		if paused {
			report.PausedCronJobs++
		}
		if err := writeFile(destination, payload); err != nil {
			return ImportReport{}, err
		}
	}
	staged := make(map[string]string)
	for _, newID := range report.AgentIDMappings {
		staged[newID] = filepath.Join(stagingRoot, newID)
		if _, err := os.Stat(filepath.Join(staged[newID], "config.yaml")); err != nil {
			return ImportReport{}, fmt.Errorf("staged profile %s is incomplete", newID)
		}
	}
	if err := ctx.Err(); err != nil {
		return ImportReport{}, err
	}
	if managedStageID != "" {
		if len(stagedTeams) > 0 && s.teams == nil {
			report.Warnings = append(report.Warnings, "team definitions were not imported because this edition has no team store")
		}
		record := managedStageRecord{StageID: managedStageID, UserID: userID, State: "staged", Report: report, Teams: stagedTeams}
		if err := s.writeManagedStage(stagingRoot, record); err != nil {
			return ImportReport{}, err
		}
		finalRoot := s.managedStageRoot(managedStageID)
		if err := os.Rename(stagingRoot, finalRoot); err != nil {
			return ImportReport{}, err
		}
		cleanupStaging = false
		return report, nil
	}
	if err := s.profiles.PublishStagedProfiles(staged); err != nil {
		return ImportReport{}, err
	}
	if len(stagedTeams) > 0 && s.teams == nil {
		report.Warnings = append(report.Warnings, "team definitions were not imported because this edition has no team store")
	} else {
		for _, team := range stagedTeams {
			if _, err := s.teams.CreateTeam(ctx, team); err != nil {
				return ImportReport{}, fmt.Errorf("publish imported team %s: %w", team.ID, err)
			}
		}
	}
	sort.Strings(report.QuarantinedCode)
	return report, nil
}

func (s *Portability) managedStageRoot(stageID string) string {
	return filepath.Join(s.profiles.DataRoot(), "imports", "managed", stageID)
}

func (s *Portability) readManagedStage(stageID string) (managedStageRecord, error) {
	payload, err := os.ReadFile(filepath.Join(s.managedStageRoot(stageID), "stage.json"))
	if err != nil {
		return managedStageRecord{}, err
	}
	var record managedStageRecord
	if err := json.Unmarshal(payload, &record); err != nil {
		return managedStageRecord{}, fmt.Errorf("decode managed import stage: %w", err)
	}
	if record.StageID != stageID || !managedStageIDPattern.MatchString(record.StageID) || record.UserID == "" || (record.State != "staged" && record.State != "committed") {
		return managedStageRecord{}, fmt.Errorf("managed import stage metadata is invalid")
	}
	return record, nil
}

func (s *Portability) writeManagedStage(root string, record managedStageRecord) error {
	payload, err := json.MarshalIndent(record, "", "  ")
	if err != nil {
		return err
	}
	payload = append(payload, '\n')
	return writeFile(filepath.Join(root, "stage.json"), payload)
}

func (s *Portability) exportProfile(ctx context.Context, archive *zip.Writer, checksums map[string]Checksum, agentID string, root string, includeConversations bool) error {
	return filepath.WalkDir(root, func(filePath string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		relative, err := filepath.Rel(root, filePath)
		if err != nil || relative == "." {
			return err
		}
		relative = filepath.ToSlash(relative)
		top := strings.Split(relative, "/")[0]
		if top == "home" || top == "logs" || top == "quarantine" || (!includeConversations && top == "sessions") {
			if entry.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if !allowedProfilePath(relative) {
			if entry.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("profile symlink cannot be exported: %s", relative)
		}
		if entry.IsDir() {
			return nil
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("non-regular profile entry cannot be exported: %s", relative)
		}
		if info.Size() > s.limits.MaxFileBytes {
			return fmt.Errorf("profile file exceeds export limit: %s", relative)
		}
		bundlePath := path.Join("profiles", agentID, relative)
		if relative == "config.yaml" {
			payload, err := os.ReadFile(filePath)
			if err != nil {
				return err
			}
			payload, err = sanitizeExportConfig(payload)
			if err != nil {
				return err
			}
			return writeBundleBytes(archive, bundlePath, payload, checksums)
		}
		file, err := os.Open(filePath)
		if err != nil {
			return err
		}
		defer file.Close()
		return writeBundleReader(archive, bundlePath, file, checksums)
	})
}

func (s *Portability) transformImportPayload(relative string, payload []byte, oldID string, newID string, userID string, agent ManifestAgent) ([]byte, bool, error) {
	switch {
	case relative == "config.yaml":
		var config contract.ProfileConfig
		if err := yaml.Unmarshal(payload, &config); err != nil {
			return nil, false, err
		}
		now := s.now()
		config.OpenLumora = contract.ProfileMetadata{SchemaVersion: 1, ID: newID, UserID: userID, Name: agent.Name, Title: agent.Title, Description: agent.Description, Status: "active", CreatedAt: agent.CreatedAt, UpdatedAt: now}
		config.Approvals.Persisted = map[string]string{}
		config.Approvals.LastUpdated = now.Format(time.RFC3339Nano)
		config.Skills.WriteApproval = true
		config.Memory.WriteApproval = true
		config.Providers = nil
		config.Model.Provider = ""
		config.Model.BaseURL = ""
		config.Model.Default = "auto"
		profilePath, _ := s.profiles.ProfilePath(newID)
		config.Terminal.CWD = filepath.Join(profilePath, "workspace")
		encoded, err := yaml.Marshal(config)
		return encoded, false, err
	case strings.HasPrefix(relative, "cron/jobs/") && strings.HasSuffix(relative, ".yaml"):
		var job contract.CronJob
		if err := yaml.Unmarshal(payload, &job); err != nil {
			return nil, false, err
		}
		job.AgentID = newID
		job.Enabled = false
		job.UpdatedAt = s.now()
		encoded, err := yaml.Marshal(job)
		return encoded, true, err
	case strings.HasSuffix(relative, "/snapshot.json"):
		var manifest map[string]any
		if err := json.Unmarshal(payload, &manifest); err != nil {
			return nil, false, err
		}
		if manifest["agent_id"] == oldID {
			manifest["agent_id"] = newID
		}
		encoded, err := json.MarshalIndent(manifest, "", "  ")
		return append(encoded, '\n'), false, err
	default:
		return payload, false, nil
	}
}

func (s *Portability) openArchive(reader io.ReaderAt, size int64) (*zip.Reader, error) {
	if reader == nil || size < 1 {
		return nil, fmt.Errorf("bundle is empty")
	}
	if size > s.limits.MaxCompressedBytes {
		return nil, fmt.Errorf("bundle compressed size exceeds limit")
	}
	archive, err := zip.NewReader(reader, size)
	if err != nil {
		return nil, fmt.Errorf("open bundle: %w", err)
	}
	return archive, nil
}

func sanitizeExportConfig(payload []byte) ([]byte, error) {
	var config contract.ProfileConfig
	if err := yaml.Unmarshal(payload, &config); err != nil {
		return nil, err
	}
	config.Providers = nil
	config.Model.BaseURL = ""
	config.Terminal.CWD = ""
	return yaml.Marshal(config)
}

func allowedProfilePath(relative string) bool {
	if relative == "config.yaml" || relative == "profile.yaml" || relative == "AGENTS.md" {
		return true
	}
	top := strings.Split(relative, "/")[0]
	allowed := map[string]bool{"skills": true, "memories": true, "workspace": true, "snapshots": true, "cron": true, "sessions": true}
	if !allowed[top] {
		return false
	}
	base := path.Base(relative)
	return !strings.HasPrefix(base, ".open-lumora-write-") && base != "state.db-wal" && base != "state.db-shm"
}

func safeArchivePath(name string, maxDepth int) (string, error) {
	if name == "" || strings.Contains(name, "\\") || strings.HasPrefix(name, "/") {
		return "", fmt.Errorf("invalid archive path %q", name)
	}
	for _, character := range name {
		if unicode.IsControl(character) {
			return "", fmt.Errorf("archive path contains control characters")
		}
	}
	trimmed := strings.TrimSuffix(name, "/")
	parts := strings.Split(trimmed, "/")
	if len(parts) > maxDepth {
		return "", fmt.Errorf("archive path depth exceeds limit")
	}
	for index, part := range parts {
		if part == "" || part == "." || part == ".." || index == 0 && strings.Contains(part, ":") {
			return "", fmt.Errorf("invalid archive path %q", name)
		}
	}
	clean := path.Clean(trimmed)
	if clean != trimmed {
		return "", fmt.Errorf("non-canonical archive path %q", name)
	}
	return clean, nil
}

func writeBundleBytes(archive *zip.Writer, name string, payload []byte, checksums map[string]Checksum) error {
	return writeBundleReader(archive, name, bytes.NewReader(payload), checksums)
}

func writeBundleReader(archive *zip.Writer, name string, reader io.Reader, checksums map[string]Checksum) error {
	header := &zip.FileHeader{Name: name, Method: zip.Deflate}
	header.SetMode(0o640)
	header.SetModTime(time.Unix(0, 0).UTC())
	writer, err := archive.CreateHeader(header)
	if err != nil {
		return err
	}
	hash := sha256.New()
	size, err := io.Copy(io.MultiWriter(writer, hash), reader)
	if err != nil {
		return err
	}
	if checksums != nil {
		checksums[name] = Checksum{SHA256: hex.EncodeToString(hash.Sum(nil)), Size: size}
	}
	return nil
}

func hashZipFile(file *zip.File, maxBytes int64) (Checksum, error) {
	reader, err := file.Open()
	if err != nil {
		return Checksum{}, err
	}
	defer reader.Close()
	hash := sha256.New()
	size, err := io.Copy(hash, io.LimitReader(reader, maxBytes+1))
	if err != nil {
		return Checksum{}, err
	}
	if size > maxBytes {
		return Checksum{}, fmt.Errorf("archive file exceeds size limit")
	}
	return Checksum{SHA256: hex.EncodeToString(hash.Sum(nil)), Size: size}, nil
}

func decodeZipJSON(file *zip.File, destination any, maxBytes int64) error {
	payload, err := readZipBytes(file, maxBytes)
	if err != nil {
		return err
	}
	if err := json.Unmarshal(payload, destination); err != nil {
		return fmt.Errorf("decode %s: %w", file.Name, err)
	}
	return nil
}

func readZipBytes(file *zip.File, maxBytes int64) ([]byte, error) {
	reader, err := file.Open()
	if err != nil {
		return nil, err
	}
	defer reader.Close()
	payload, err := io.ReadAll(io.LimitReader(reader, maxBytes+1))
	if err != nil {
		return nil, err
	}
	if int64(len(payload)) > maxBytes {
		return nil, fmt.Errorf("archive file exceeds size limit")
	}
	return payload, nil
}

func writeFile(name string, payload []byte) error {
	temporary, err := os.CreateTemp(filepath.Dir(name), ".open-lumora-import-*")
	if err != nil {
		return err
	}
	temporaryName := temporary.Name()
	defer os.Remove(temporaryName)
	if err := temporary.Chmod(0o640); err != nil {
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
	return os.Rename(temporaryName, name)
}

func isCustomCode(name string) bool {
	if !strings.Contains(name, "/workspace/") {
		return false
	}
	extension := strings.ToLower(path.Ext(name))
	return map[string]bool{".sh": true, ".bash": true, ".py": true, ".js": true, ".mjs": true, ".ts": true, ".exe": true, ".bat": true, ".cmd": true, ".ps1": true}[extension]
}

func uniqueStrings(values []string) []string {
	seen := make(map[string]bool)
	result := make([]string, 0, len(values))
	for _, value := range values {
		if !seen[value] {
			seen[value] = true
			result = append(result, value)
		}
	}
	return result
}

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

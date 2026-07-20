// <Summary>
// Package api owns the Fiber HTTP boundary and stable response envelopes.
// File: Server.go
// Functions:
//   - NewApp(config config.Config, policy edition.Policy, agents *services.Agents, conversations *services.Conversations) *fiber.App
//
// </Summary>
package api

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"os"
	"os/exec"
	"path/filepath"
	stdruntime "runtime"
	"strconv"
	"strings"
	"time"

	otelfiber "github.com/gofiber/contrib/v3/otel"
	"github.com/gofiber/fiber/v3"
	"github.com/gofiber/fiber/v3/middleware/cors"
	recovermiddleware "github.com/gofiber/fiber/v3/middleware/recover"
	"github.com/rs/zerolog/log"
	"github.com/xno/open-lumora/internal/config"
	"github.com/xno/open-lumora/internal/edition"
	"github.com/xno/open-lumora/internal/limits"
	"github.com/xno/open-lumora/internal/middlewares"
	"github.com/xno/open-lumora/internal/models"
	"github.com/xno/open-lumora/internal/ninerouter"
	"github.com/xno/open-lumora/internal/profile"
	"github.com/xno/open-lumora/internal/repositories"
	runtimeadapter "github.com/xno/open-lumora/internal/runtime"
	"github.com/xno/open-lumora/internal/studio/contract"
	"github.com/xno/open-lumora/services/agents"
	"github.com/xno/open-lumora/services/conversations"
	"github.com/xno/open-lumora/services/crons"
	"github.com/xno/open-lumora/services/portability"
	"github.com/xno/open-lumora/services/teams"
	"go.opentelemetry.io/otel/trace"
)

type Server struct {
	config        config.Config
	policy        edition.Policy
	agents        *agents.Agents
	conversations *conversations.Conversations
	crons         *crons.Crons
	portability   *portability.Portability
	teams         *teams.Teams
	connector     contract.Connector
	router        *ninerouter.Client
	startedAt     time.Time
}

type envelope struct {
	Success    bool   `json:"success"`
	Data       any    `json:"data,omitempty"`
	Message    string `json:"message,omitempty"`
	StatusCode int    `json:"status_code"`
	Pagination any    `json:"pagination,omitempty"`
}

func NewServer(cfg config.Config, policy edition.Policy, agentService *agents.Agents, conversationService *conversations.Conversations, cronService *crons.Crons) *Server {
	return &Server{config: cfg, policy: policy, agents: agentService, conversations: conversationService, crons: cronService, router: ninerouter.New(cfg.NineRouterURL, cfg.NineRouterDataDir), startedAt: time.Now().UTC()}
}

func (s *Server) SetPortability(service *portability.Portability) { s.portability = service }

func (s *Server) SetTeams(service *teams.Teams) { s.teams = service }

func (s *Server) SetConnector(connector contract.Connector) { s.connector = connector }

func NewApp(cfg config.Config, server *Server) *fiber.App {
	app := fiber.New(fiber.Config{BodyLimit: 1024 * 1024 * 1024, EnableIPValidation: true})
	app.Use(recovermiddleware.New())
	app.Use(cors.New(cors.Config{
		AllowOrigins: cfg.CORSAllowedOrigins,
		AllowMethods: []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
		AllowHeaders: []string{"Origin", "Content-Type", "Accept", "Authorization", "traceparent"},
	}))
	// Fiber's send-file response body is a pooled stream. The current OTel
	// metrics wrapper closes that stream twice; retain tracing and disable only
	// the middleware's body-size metrics until upstream removes the wrapper.
	app.Use(otelfiber.Middleware(otelfiber.WithoutMetrics(true)))
	app.Use(server.loggingMiddleware)
	return app
}

func (s *Server) loggingMiddleware(c fiber.Ctx) error {
	started := time.Now()
	err := c.Next()
	status := c.Response().StatusCode()
	event := log.Info()
	if err != nil || status >= fiber.StatusInternalServerError {
		event = log.Error().Err(err)
	} else if status >= fiber.StatusBadRequest {
		event = log.Warn()
	}
	spanContext := trace.SpanContextFromContext(c.Context())
	if spanContext.IsValid() {
		event = event.Str("trace_id", spanContext.TraceID().String()).Str("span_id", spanContext.SpanID().String())
	}
	route := c.Route().Path
	if route == "" {
		route = "unmatched"
	}
	event.Str("method", c.Method()).Str("route", route).Int("status", status).Dur("latency", time.Since(started)).Msg("http.request")
	return err
}

func (s *Server) principal(c fiber.Ctx) (edition.Principal, error) {
	authorization := c.Get("Authorization")
	return s.policy.Principal(c.Context(), authorization)
}

func (s *Server) Health(c fiber.Ctx) error {
	return send(c, fiber.StatusOK, map[string]any{"status": "ok", "edition": s.policy.Name()}, "healthy")
}

func (s *Server) RegisterAgentRoutes(app *fiber.App) {
	root := app.Group("/agent-gateway/v1")
	root.Get("/agents", s.listAgents)
	root.Post("/agents", middlewares.RateLimit(s.agentCreateLimit), s.createAgent)
	root.Get("/agents/:agent_id/detail", s.getAgent)
	root.Patch("/agents/:agent_id/metadata", s.updateAgentMetadata)
	root.Delete("/agents/:agent_id/delete", s.deleteAgent)
	root.Post("/agents/:agent_id/test", s.testAgent)
	root.Get("/agents/:agent_id/runtime", s.getAgentRuntime)
	root.Get("/agents-configs/global", s.getRootConfig)
	root.Patch("/agents-configs/global", s.updateRootConfig)
	root.Patch("/agents-configs/:agent_id", s.updateAgentConfig)
	root.Get("/agents-skills/:agent_id", s.listSkills)
	root.Post("/agents-skills/:agent_id", s.installSkill)
	root.Patch("/agents-skills/:agent_id/:skill_id", s.setSkillEnabled)
	root.Delete("/agents-skills/:agent_id/:skill_id", s.removeSkill)
	root.Get("/agents/:agent_id/memory", s.readMemory)
	root.Patch("/agents/:agent_id/memory", s.writeMemory)
	root.Get("/agents/:agent_id/snapshots", s.listSnapshots)
	root.Post("/agents/:agent_id/snapshots/:snapshot_id/restore", s.restoreSnapshot)
	root.Get("/agents-workspaces/:agent_id", s.listWorkspace)
	root.Get("/agents-workspaces/:agent_id/file", s.viewWorkspaceFile)
	root.Post("/agents-workspaces/:agent_id/read", s.readWorkspaceFile)
	root.Post("/agents-workspaces/:agent_id/write", s.writeWorkspaceFile)
	root.Post("/agents-workspaces/:agent_id/create", s.createWorkspacePath)
	root.Post("/agents-workspaces/:agent_id/delete", s.deleteWorkspacePath)
	root.Post("/agents-workspaces/:agent_id/upload", s.uploadWorkspaceFile)
}

func (s *Server) listAgents(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	agents, err := s.agents.List(c.Context(), principal.UserID)
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, agents, "agents retrieved successfully")
}

func (s *Server) createAgent(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	var request struct {
		Name        string `json:"name"`
		Description string `json:"description"`
	}
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	agent, err := s.agents.Create(c.Context(), principal.UserID, request.Name, request.Description)
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusCreated, agent, "agent created successfully")
}

func (s *Server) getAgent(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	agent, err := s.agents.Get(c.Context(), principal.UserID, c.Params("agent_id"))
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, agent, "agent retrieved successfully")
}

func (s *Server) updateAgentMetadata(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	var request struct {
		Title       *string `json:"title"`
		Description *string `json:"description"`
	}
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	agent, err := s.agents.UpdateMetadata(c.Context(), principal.UserID, c.Params("agent_id"), request.Title, request.Description)
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, agent, "agent updated successfully")
}

func (s *Server) deleteAgent(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	if err := s.agents.Delete(c.Context(), principal.UserID, c.Params("agent_id")); err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, map[string]any{"deleted": true}, "agent deleted successfully")
}

func (s *Server) testAgent(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	agent, err := s.agents.Get(c.Context(), principal.UserID, c.Params("agent_id"))
	if err != nil {
		return sendError(c, err)
	}
	path, _ := s.agents.Profiles().ProfilePath(agent.ID)
	return send(c, fiber.StatusOK, map[string]any{"agent_id": agent.ID, "healthy": true, "status": "ok", "message": "profile is ready", "profile_path": path}, "agent tested successfully")
}

func (s *Server) getAgentRuntime(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	agent, err := s.agents.Get(c.Context(), principal.UserID, c.Params("agent_id"))
	if err != nil {
		return sendError(c, err)
	}
	memory, err := s.agents.ReadMemory(c.Context(), principal.UserID, agent.ID)
	if err != nil {
		return sendError(c, err)
	}
	skills, err := s.agents.ListSkills(c.Context(), principal.UserID, agent.ID)
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, map[string]any{"config": agent.Config, "memory": memory, "skills": skills, "profile_path": agent.Metadata["profile_path"]}, "runtime retrieved successfully")
}

func (s *Server) getRootConfig(c fiber.Ctx) error {
	config, err := s.agents.Profiles().ReadRootConfig()
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, config, "global config retrieved successfully")
}

func (s *Server) updateRootConfig(c fiber.Ctx) error {
	var request configRequest
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	config, err := s.agents.Profiles().UpdateRootConfig(request.patch())
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, config, "global config updated successfully")
}

func (s *Server) updateAgentConfig(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	var request configRequest
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	config, err := s.agents.UpdateConfig(c.Context(), principal.UserID, c.Params("agent_id"), request.patch())
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, config, "agent config updated successfully")
}

type configRequest struct {
	Provider            *string `json:"provider"`
	Model               *string `json:"model"`
	ReasoningEffort     *string `json:"reasoning_effort"`
	ApprovalMode        *string `json:"approval_mode"`
	SkillsWriteApproval *bool   `json:"skills_write_approval"`
	MemoryWriteApproval *bool   `json:"memory_write_approval"`
	SystemPrompt        *string `json:"system_prompt"`
}

func (r configRequest) patch() profile.ConfigPatch {
	return profile.ConfigPatch{Provider: r.Provider, Model: r.Model, ReasoningEffort: r.ReasoningEffort, ApprovalMode: r.ApprovalMode, SkillsWriteApproval: r.SkillsWriteApproval, MemoryWriteApproval: r.MemoryWriteApproval, SystemPrompt: r.SystemPrompt}
}

func (s *Server) listSkills(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	skills, err := s.agents.ListSkills(c.Context(), principal.UserID, c.Params("agent_id"))
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, map[string]any{"agent_id": c.Params("agent_id"), "skills": skills}, "skills retrieved successfully")
}

func (s *Server) installSkill(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	var request struct {
		SkillID  string `json:"skill_id"`
		Name     string `json:"name"`
		Category string `json:"category"`
		Content  string `json:"content"`
	}
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	skills, err := s.agents.InstallSkill(c.Context(), principal.UserID, c.Params("agent_id"), request.SkillID, request.Name, request.Category, request.Content)
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, map[string]any{"agent_id": c.Params("agent_id"), "skills": skills}, "skill installed successfully")
}

func (s *Server) setSkillEnabled(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	var request struct {
		Enabled *bool `json:"enabled"`
	}
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	if request.Enabled == nil {
		return sendError(c, fmt.Errorf("enabled is required"))
	}
	skills, err := s.agents.SetSkillEnabled(c.Context(), principal.UserID, c.Params("agent_id"), c.Params("skill_id"), *request.Enabled)
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, map[string]any{"agent_id": c.Params("agent_id"), "skills": skills}, "skill updated successfully")
}

func (s *Server) removeSkill(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	skills, err := s.agents.RemoveSkill(c.Context(), principal.UserID, c.Params("agent_id"), c.Params("skill_id"))
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, map[string]any{"agent_id": c.Params("agent_id"), "skills": skills}, "skill removed successfully")
}

func (s *Server) readMemory(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	memory, err := s.agents.ReadMemory(c.Context(), principal.UserID, c.Params("agent_id"))
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, memory, "memory retrieved successfully")
}

func (s *Server) writeMemory(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	var request struct {
		Memory  *string `json:"memory"`
		User    *string `json:"user"`
		Target  string  `json:"target"`
		Content string  `json:"content"`
		Append  bool    `json:"append"`
	}
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	target, content := request.Target, request.Content
	if request.Memory != nil {
		target, content = "memory", *request.Memory
	}
	if request.User != nil {
		target, content = "user", *request.User
	}
	if target == "" {
		target = "memory"
	}
	memory, snapshot, err := s.agents.WriteMemory(c.Context(), principal.UserID, c.Params("agent_id"), target, content, request.Append)
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, map[string]any{"memory": memory["memory"], "user": memory["user"], "snapshot": snapshot}, "memory updated successfully")
}

func (s *Server) listSnapshots(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	if err := s.agents.EnsureOwned(c.Context(), principal.UserID, c.Params("agent_id")); err != nil {
		return sendError(c, err)
	}
	snapshots, err := s.agents.Profiles().ListSnapshots(c.Params("agent_id"), c.Query("kind"))
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, snapshots, "snapshots retrieved successfully")
}

func (s *Server) restoreSnapshot(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	if err := s.agents.EnsureOwned(c.Context(), principal.UserID, c.Params("agent_id")); err != nil {
		return sendError(c, err)
	}
	snapshot, err := s.agents.Profiles().RestoreSnapshot(c.Params("agent_id"), c.Params("snapshot_id"))
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, snapshot, "snapshot restored successfully")
}

func (s *Server) listWorkspace(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	if err := s.agents.EnsureOwned(c.Context(), principal.UserID, c.Params("agent_id")); err != nil {
		return sendError(c, err)
	}
	entries, err := s.agents.Profiles().ListWorkspace(c.Params("agent_id"), c.Query("path"))
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, map[string]any{"entries": entries}, "workspace retrieved successfully")
}

func (s *Server) viewWorkspaceFile(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	if err := s.agents.EnsureOwned(c.Context(), principal.UserID, c.Params("agent_id")); err != nil {
		return sendError(c, err)
	}
	payload, err := s.agents.Profiles().ReadWorkspace(c.Params("agent_id"), c.Query("path"))
	if err != nil {
		return sendError(c, err)
	}
	contentType := mime.TypeByExtension(filepath.Ext(c.Query("path")))
	if contentType == "" {
		contentType = "application/octet-stream"
	}
	c.Set(fiber.HeaderContentType, contentType)
	return c.Send(payload)
}

type workspaceRequest struct {
	Path    string `json:"path"`
	Type    string `json:"type"`
	Content string `json:"content"`
}

func (s *Server) readWorkspaceFile(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	var request workspaceRequest
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	if err := s.agents.EnsureOwned(c.Context(), principal.UserID, c.Params("agent_id")); err != nil {
		return sendError(c, err)
	}
	payload, err := s.agents.Profiles().ReadWorkspace(c.Params("agent_id"), request.Path)
	if err != nil {
		return sendError(c, err)
	}
	return send(c, 200, map[string]any{"path": request.Path, "content": string(payload)}, "file read successfully")
}
func (s *Server) writeWorkspaceFile(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	var request workspaceRequest
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	if err := s.agents.EnsureOwned(c.Context(), principal.UserID, c.Params("agent_id")); err != nil {
		return sendError(c, err)
	}
	if err := s.agents.Profiles().WriteWorkspace(c.Params("agent_id"), request.Path, []byte(request.Content)); err != nil {
		return sendError(c, err)
	}
	return send(c, 200, map[string]any{"path": request.Path}, "file written successfully")
}
func (s *Server) createWorkspacePath(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	var request workspaceRequest
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	if err := s.agents.EnsureOwned(c.Context(), principal.UserID, c.Params("agent_id")); err != nil {
		return sendError(c, err)
	}
	if request.Type == "directory" {
		err = s.agents.Profiles().CreateWorkspaceDirectory(c.Params("agent_id"), request.Path)
	} else {
		err = s.agents.Profiles().WriteWorkspace(c.Params("agent_id"), request.Path, []byte(request.Content))
	}
	if err != nil {
		return sendError(c, err)
	}
	return send(c, 201, map[string]any{"path": request.Path, "type": request.Type}, "path created successfully")
}
func (s *Server) deleteWorkspacePath(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	var request workspaceRequest
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	if err := s.agents.EnsureOwned(c.Context(), principal.UserID, c.Params("agent_id")); err != nil {
		return sendError(c, err)
	}
	if err := s.agents.Profiles().DeleteWorkspace(c.Params("agent_id"), request.Path); err != nil {
		return sendError(c, err)
	}
	return send(c, 200, map[string]any{"deleted": true}, "path deleted successfully")
}

func (s *Server) uploadWorkspaceFile(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	if err := s.agents.EnsureOwned(c.Context(), principal.UserID, c.Params("agent_id")); err != nil {
		return sendError(c, err)
	}
	file, err := c.FormFile("file")
	if err != nil {
		return sendError(c, fmt.Errorf("file is required"))
	}
	directory := strings.TrimSpace(c.FormValue("path"))
	if directory == "." {
		directory = ""
	}
	payload, err := file.Open()
	if err != nil {
		return sendError(c, err)
	}
	defer payload.Close()
	content, err := io.ReadAll(io.LimitReader(payload, 1024*1024*1024))
	if err != nil {
		return sendError(c, err)
	}
	target := filepath.ToSlash(filepath.Join(directory, filepath.Base(file.Filename)))
	if err := s.agents.Profiles().WriteWorkspace(c.Params("agent_id"), target, content); err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusCreated, map[string]any{"path": target}, "file uploaded successfully")
}

func (s *Server) RegisterConversationRoutes(app *fiber.App) {
	root := app.Group("/conversations/v1/conversations")
	root.Get("/", s.listConversations)
	root.Get("", s.listConversations)
	root.Post("/", s.createConversation)
	root.Post("", s.createConversation)
	root.Get("/:id/detail", s.getConversation)
	root.Get("/:id/messages", s.listMessages)
	root.Get("/:id/usage", s.getUsage)
	root.Patch("/:id/name", s.renameConversation)
	root.Delete("/:id/delete", s.deleteConversation)
	root.Post("/:id/chat/stream", s.streamConversation)
	root.Post("/:id/runs/:run_id/stop", s.stopRun)
	root.Post("/:id/runs/:run_id/approval", s.resolveApproval)
}

func (s *Server) conversationIdentity(c fiber.Ctx) (edition.Principal, string, error) {
	principal, err := s.principal(c)
	agentID := strings.TrimSpace(c.Query("agent"))
	if agentID == "" {
		return principal, "", fmt.Errorf("agent is required")
	}
	return principal, agentID, err
}
func (s *Server) listConversations(c fiber.Ctx) error {
	p, a, e := s.conversationIdentity(c)
	if e != nil {
		return sendError(c, e)
	}
	items, e := s.conversations.List(c.Context(), p.UserID, a)
	if e != nil {
		return sendError(c, e)
	}
	summaries := make([]map[string]any, 0, len(items))
	for _, item := range items {
		summaries = append(summaries, conversationDTO(item))
	}
	return send(c, 200, map[string]any{"conversations": summaries, "pagination": map[string]any{"page": 1, "limit": 50, "has_more": false}}, "conversations retrieved successfully")
}
func (s *Server) createConversation(c fiber.Ctx) error {
	p, a, e := s.conversationIdentity(c)
	if e != nil {
		return sendError(c, e)
	}
	var req struct {
		Title string `json:"title"`
	}
	if len(c.Body()) > 0 {
		if e = bind(c, &req); e != nil {
			return sendError(c, e)
		}
	}
	item, e := s.conversations.Create(c.Context(), p.UserID, a, req.Title)
	if e != nil {
		return sendError(c, e)
	}
	return send(c, 201, conversationDTO(item), "conversation created successfully")
}
func (s *Server) getConversation(c fiber.Ctx) error {
	p, a, e := s.conversationIdentity(c)
	if e != nil {
		return sendError(c, e)
	}
	item, e := s.conversations.Get(c.Context(), p.UserID, a, c.Params("id"))
	if e != nil {
		return sendError(c, e)
	}
	return send(c, 200, conversationDTO(item), "conversation retrieved successfully")
}
func (s *Server) listMessages(c fiber.Ctx) error {
	p, a, e := s.conversationIdentity(c)
	if e != nil {
		return sendError(c, e)
	}
	items, e := s.conversations.Messages(c.Context(), p.UserID, a, c.Params("id"))
	if e != nil {
		return sendError(c, e)
	}
	return send(c, 200, map[string]any{"messages": items}, "messages retrieved successfully")
}
func (s *Server) renameConversation(c fiber.Ctx) error {
	p, a, e := s.conversationIdentity(c)
	if e != nil {
		return sendError(c, e)
	}
	var req struct {
		Title string `json:"title"`
	}
	if e = bind(c, &req); e != nil {
		return sendError(c, e)
	}
	item, e := s.conversations.Rename(c.Context(), p.UserID, a, c.Params("id"), req.Title)
	if e != nil {
		return sendError(c, e)
	}
	return send(c, 200, conversationDTO(item), "conversation renamed successfully")
}
func (s *Server) deleteConversation(c fiber.Ctx) error {
	p, a, e := s.conversationIdentity(c)
	if e != nil {
		return sendError(c, e)
	}
	if e = s.conversations.Delete(c.Context(), p.UserID, a, c.Params("id")); e != nil {
		return sendError(c, e)
	}
	return send(c, 200, map[string]any{"deleted": true}, "conversation deleted successfully")
}
func (s *Server) getUsage(c fiber.Ctx) error {
	p, a, e := s.conversationIdentity(c)
	if e != nil {
		return sendError(c, e)
	}
	messages, e := s.conversations.Messages(c.Context(), p.UserID, a, c.Params("id"))
	if e != nil {
		return sendError(c, e)
	}
	return send(c, 200, map[string]any{"conversation_id": c.Params("id"), "messages": len(messages), "api_calls": 0, "model": "", "tokens": map[string]int{"total": 0}, "cost": map[string]any{"total_usd": 0}, "account": map[string]any{"available": false, "message": "Provider quota is reported by 9router when connected", "limits": []any{}}}, "usage retrieved successfully")
}

func (s *Server) streamConversation(c fiber.Ctx) error {
	p, a, e := s.conversationIdentity(c)
	if e != nil {
		return sendError(c, e)
	}
	var req struct {
		Input string `json:"input"`
	}
	if e = bind(c, &req); e != nil {
		return sendError(c, e)
	}
	c.Set(fiber.HeaderContentType, "text/event-stream")
	c.Set(fiber.HeaderCacheControl, "no-cache")
	c.Set("X-Accel-Buffering", "no")
	// Fiber runs the stream writer after the handler returns and recycles Ctx;
	// capture route values now instead of reading them inside the callback.
	conversationID := c.Params("id")
	streamContext := context.Background()
	return c.SendStreamWriter(func(writer *bufio.Writer) {
		emit := func(event runtimeadapter.Event) {
			payload := eventPayload(event)
			encoded, _ := json.Marshal(payload)
			_, _ = writer.WriteString("data: " + string(encoded) + "\n\n")
			_ = writer.Flush()
		}
		if err := s.conversations.Stream(streamContext, p.UserID, a, conversationID, req.Input, emit); err != nil {
			emit(runtimeadapter.Event{Type: "error", Text: err.Error()})
		}
	})
}
func (s *Server) stopRun(c fiber.Ctx) error {
	if e := s.conversations.Stop(c.Params("run_id")); e != nil {
		return sendError(c, e)
	}
	return send(c, 200, map[string]any{"stopped": true}, "run stopped successfully")
}
func (s *Server) resolveApproval(c fiber.Ctx) error {
	p, a, e := s.conversationIdentity(c)
	if e != nil {
		return sendError(c, e)
	}
	var req struct {
		Choice     string `json:"choice"`
		ResolveAll bool   `json:"resolve_all"`
		Subsystem  string `json:"subsystem"`
	}
	if e = bind(c, &req); e != nil {
		return sendError(c, e)
	}
	if e = s.conversations.ResolveApproval(c.Context(), p.UserID, a, c.Params("run_id"), req.Choice, req.ResolveAll, req.Subsystem); e != nil {
		return sendError(c, e)
	}
	return send(c, 200, map[string]any{"run_id": c.Params("run_id"), "choice": req.Choice, "resolved": 1}, "approval resolved successfully")
}

func eventPayload(event runtimeadapter.Event) map[string]any {
	payload := make(map[string]any, len(event.Payload)+3)
	for key, value := range event.Payload {
		payload[key] = value
	}
	payload["event"] = event.Type
	payload["run_id"] = event.RunID
	if _, exists := payload["timestamp"]; !exists {
		payload["timestamp"] = float64(time.Now().UnixNano()) / 1e9
	}
	if event.Type == "assistant.delta" || event.Type == "response.output_text.delta" {
		payload["event"] = "assistant.delta"
		payload["delta"] = event.Text
	} else if event.Type == "run.completed" {
		payload["output"] = event.Text
	} else if event.Type == "error" || event.Type == "run.failed" {
		payload["message"] = event.Text
	} else if event.Type == "approval.required" || event.Type == "approval.request" {
		allowPermanent := event.AllowPermanent || profileWriteApproval(event)
		payload["event"] = "approval.request"
		payload["command"] = event.Command
		payload["description"] = event.Description
		payload["pattern_key"] = event.PatternKey
		payload["pattern_keys"] = event.PatternKeys
		payload["allow_permanent"] = allowPermanent
		payload["choices"] = approvalChoices(allowPermanent)
	}
	return payload
}

func profileWriteApproval(event runtimeadapter.Event) bool {
	for _, key := range append(append([]string{}, event.PatternKeys...), event.PatternKey) {
		if key == "memory_write" || key == "skills_write" {
			return true
		}
	}
	return false
}

func approvalChoices(allowPermanent bool) []string {
	if allowPermanent {
		return []string{"once", "always", "deny"}
	}
	return []string{"once", "deny"}
}
func conversationDTO(item models.Conversation) map[string]any {
	return map[string]any{"id": item.ID, "title": item.Title, "model": item.Model, "preview": item.Preview, "message_count": item.Messages, "tool_call_count": item.Tools, "started_at": float64(item.CreatedAt.UnixNano()) / 1e9, "last_active_at": float64(item.UpdatedAt.UnixNano()) / 1e9}
}

func (s *Server) RegisterSandboxRoutes(app *fiber.App) {
	root := app.Group("/sandboxes/v1/me/sandboxes")
	root.Get("/info", s.sandboxInfo)
	root.Get("/metrics", s.sandboxMetrics)
	root.Get("/stats", s.sandboxStats)
	root.Get("/health", s.sandboxHealth)
	root.Post("/setup", s.sandboxSetup)
}
func (s *Server) sandboxInfo(c fiber.Ctx) error {
	runtimeType := "host"
	image := "local Hermes CLI"
	if s.config.HermesRuntimeURL != "" {
		runtimeType = "container"
		image = "open-lumora-hermes-runtime"
	}
	return send(c, 200, map[string]any{"id": "personal-runtime", "status": "running", "type": runtimeType, "image": image, "ipv4": "private", "created_at": s.startedAt, "gateway": map[string]any{"healthy": true, "port": 8642}, "mode": s.config.StartMode, "idle_policy": map[string]any{"enabled": s.config.ContainerIdleEnabled, "timeout_seconds": int(s.config.ContainerIdleTimeout.Seconds()), "runtime_processes": "request-scoped"}, "resources": map[string]any{"cpus": stdruntime.NumCPU(), "memory": "host limit", "root_size": "data volume"}}, "runtime info")
}
func (s *Server) sandboxMetrics(c fiber.Ctx) error {
	var memory stdruntime.MemStats
	stdruntime.ReadMemStats(&memory)
	return send(c, 200, map[string]any{"cpu_percent": 0, "memory_bytes": memory.Alloc, "memory_limit_bytes": 0, "uptime_seconds": int(time.Since(s.startedAt).Seconds())}, "runtime metrics")
}
func (s *Server) sandboxStats(c fiber.Ctx) error {
	hostname, _ := os.Hostname()
	return send(c, 200, map[string]any{"system": map[string]any{"cpu_percent": 0, "processes": 1, "os": map[string]string{"hostname": hostname, "os": stdruntime.GOOS, "architecture": stdruntime.GOARCH}, "storage": map[string]any{"data_dir": s.config.DataDir}, "top_processes": []any{}}}, "runtime stats")
}
func (s *Server) sandboxHealth(c fiber.Ctx) error {
	if s.config.HermesRuntimeURL != "" {
		status, endpoint, err := s.runtimeHealth(c.Context())
		if err != nil {
			if status == 0 {
				status = fiber.StatusServiceUnavailable
			}
			return send(c, fiber.StatusServiceUnavailable, map[string]any{"healthy": false, "status_code": status, "endpoint": endpoint, "message": err.Error()}, "runtime unavailable")
		}
		return send(c, fiber.StatusOK, map[string]any{"healthy": true, "status_code": status, "endpoint": endpoint}, "runtime healthy")
	}
	_, err := exec.LookPath(s.config.HermesBin)
	if err != nil {
		return send(c, fiber.StatusServiceUnavailable, map[string]any{"healthy": false, "status_code": fiber.StatusServiceUnavailable, "endpoint": "local", "message": "Hermes CLI is not installed"}, "runtime unavailable")
	}
	return send(c, 200, map[string]any{"healthy": true, "status_code": 200, "endpoint": "local", "hermes_bin": s.config.HermesBin}, "runtime healthy")
}
func (s *Server) sandboxSetup(c fiber.Ctx) error {
	if strings.Contains(c.Get("Accept"), "text/event-stream") {
		c.Set(fiber.HeaderContentType, "text/event-stream")
		return c.SendStreamWriter(func(w *bufio.Writer) {
			for _, v := range []int{10, 40, 70, 100} {
				_, _ = w.WriteString("data: " + strconv.Itoa(v) + "\n\n")
				_ = w.Flush()
			}
		})
	}
	return send(c, 200, map[string]any{"ready": true}, "runtime ready")
}

func (s *Server) RegisterCronRoutes(app *fiber.App) {
	app.Get("/api/v1/notifications", s.listNotifications)
	app.Post("/api/v1/notifications/:notification_id/resolve", s.resolveNotification)
	root := app.Group("/agent-gateway/v1/cron/jobs")
	root.Get("/", s.listCrons)
	root.Get("", s.listCrons)
	root.Post("/", middlewares.RateLimit(s.cronCreateLimit), s.createCron)
	root.Post("", middlewares.RateLimit(s.cronCreateLimit), s.createCron)
	root.Post("/:job_id/pause", s.pauseCron)
	root.Post("/:job_id/resume", s.resumeCron)
	root.Post("/:job_id/run", middlewares.RateLimit(s.cronParallelLimit), middlewares.RateLimit(s.cronDailyLimit), middlewares.RateLimit(s.cronMonthlyLimit), s.runCron)
	root.Delete("/:job_id", s.deleteCron)
}

func (s *Server) listNotifications(c fiber.Ctx) error {
	p, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	notifications, err := s.crons.ListNotifications(c.Context(), p.UserID)
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, notifications, "notifications retrieved successfully")
}

func (s *Server) resolveNotification(c fiber.Ctx) error {
	p, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	var request struct {
		Action string `json:"action"`
	}
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	notification, err := s.crons.ResolveNotification(c.Context(), p.UserID, c.Params("notification_id"), request.Action)
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, notification, "notification resolved successfully")
}
func (s *Server) listCrons(c fiber.Ctx) error {
	p, e := s.principal(c)
	if e != nil {
		return sendError(c, e)
	}
	jobs, e := s.crons.List(c.Context(), p.UserID)
	if e != nil {
		return sendError(c, e)
	}
	return send(c, 200, jobs, "cron jobs retrieved successfully")
}
func (s *Server) createCron(c fiber.Ctx) error {
	p, e := s.principal(c)
	if e != nil {
		return sendError(c, e)
	}
	var req struct {
		AgentID         string `json:"agent_id"`
		Name            string `json:"name"`
		Prompt          string `json:"prompt"`
		IntervalMinutes int    `json:"interval_minutes"`
		Schedule        string `json:"schedule"`
		Timezone        string `json:"timezone"`
		Mode            string `json:"mode"`
		MisfirePolicy   string `json:"misfire_policy"`
		ReplayLimit     int    `json:"replay_limit"`
	}
	if e = bind(c, &req); e != nil {
		return sendError(c, e)
	}
	var job models.CronJob
	if strings.TrimSpace(req.Schedule) != "" {
		job, e = s.crons.CreateScheduled(c.Context(), p.UserID, req.AgentID, req.Name, req.Prompt, req.Schedule, req.Timezone, req.Mode, req.MisfirePolicy, req.ReplayLimit)
	} else {
		job, e = s.crons.Create(c.Context(), p.UserID, req.AgentID, req.Name, req.Prompt, req.IntervalMinutes)
	}
	if e != nil {
		return sendError(c, e)
	}
	return send(c, 201, job, "cron job created successfully")
}
func (s *Server) pauseCron(c fiber.Ctx) error  { return s.setCronEnabled(c, false) }
func (s *Server) resumeCron(c fiber.Ctx) error { return s.setCronEnabled(c, true) }
func (s *Server) setCronEnabled(c fiber.Ctx, enabled bool) error {
	p, e := s.principal(c)
	if e != nil {
		return sendError(c, e)
	}
	job, e := s.crons.SetEnabled(c.Context(), p.UserID, c.Params("job_id"), enabled)
	if e != nil {
		return sendError(c, e)
	}
	return send(c, 200, job, "cron job updated successfully")
}
func (s *Server) runCron(c fiber.Ctx) error {
	p, e := s.principal(c)
	if e != nil {
		return sendError(c, e)
	}
	if e = s.crons.RunNow(c.Context(), p.UserID, c.Params("job_id")); e != nil {
		return sendError(c, e)
	}
	return send(c, 202, map[string]any{"started": true}, "cron job started")
}
func (s *Server) deleteCron(c fiber.Ctx) error {
	p, e := s.principal(c)
	if e != nil {
		return sendError(c, e)
	}
	if e = s.crons.Delete(c.Context(), p.UserID, c.Params("job_id")); e != nil {
		return sendError(c, e)
	}
	return send(c, 200, map[string]any{"deleted": true}, "cron job deleted")
}

func (s *Server) RegisterFrontend(app *fiber.App) {
	index := filepath.Join(s.config.FrontendDir, "index.html")
	if _, err := os.Stat(index); err != nil {
		return
	}
	app.Get("/*", func(c fiber.Ctx) error {
		requested := filepath.Clean(filepath.Join(s.config.FrontendDir, c.Params("*")))
		if strings.HasPrefix(requested, s.config.FrontendDir+string(os.PathSeparator)) {
			if info, err := os.Stat(requested); err == nil && !info.IsDir() {
				return c.SendFile(requested)
			}
		}
		return c.SendFile(index)
	})
}

func bind(c fiber.Ctx, output any) error {
	if len(c.Body()) == 0 {
		return fmt.Errorf("request body is required")
	}
	if err := c.Bind().JSON(output); err != nil {
		return fmt.Errorf("invalid JSON request: %w", err)
	}
	return nil
}
func send(c fiber.Ctx, status int, data any, message string) error {
	return c.Status(status).JSON(envelope{Success: true, Data: data, Message: message, StatusCode: status})
}
func sendError(c fiber.Ctx, err error) error {
	status := fiber.StatusBadRequest
	var fiberError *fiber.Error
	if errors.As(err, &fiberError) {
		status = fiberError.Code
	} else if exceeded, ok := limits.AsExceeded(err); ok {
		status = fiber.StatusTooManyRequests
		c.Set("RateLimit-Limit", strconv.Itoa(exceeded.Decision.Limit))
		c.Set("RateLimit-Remaining", strconv.Itoa(exceeded.Decision.Remaining))
	} else if errors.Is(err, repositories.ErrNotFound) || agents.IsNotFound(err) {
		status = fiber.StatusNotFound
	} else if strings.Contains(err.Error(), "at most") || strings.Contains(err.Error(), "already exists") {
		status = fiber.StatusConflict
	} else if strings.Contains(err.Error(), "not installed") || strings.Contains(err.Error(), "runtime failed") {
		status = fiber.StatusBadGateway
	}
	return c.Status(status).JSON(envelope{Success: false, Message: err.Error(), StatusCode: status})
}

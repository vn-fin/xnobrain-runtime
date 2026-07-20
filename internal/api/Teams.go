// <Summary>
// Teams exposes saved team CRUD and synchronous bounded delegation runs through
// the stable API envelope. Service validation remains the enforcement authority.
// </Summary>
package api

import (
	"fmt"
	"strings"

	"github.com/gofiber/fiber/v3"
	"github.com/xno/open-lumora/internal/middlewares"
	"github.com/xno/open-lumora/internal/models"
)

type teamRequest struct {
	Name            string              `json:"name"`
	OrchestratorID  string              `json:"orchestrator_id"`
	Members         []models.TeamMember `json:"members"`
	SharedWorkspace bool                `json:"shared_workspace"`
	MaxParallel     int                 `json:"max_parallel"`
	MaxDepth        int                 `json:"max_depth"`
	Enabled         *bool               `json:"enabled"`
}

func (s *Server) RegisterTeamRoutes(app *fiber.App) {
	if s.teams == nil {
		return
	}
	root := app.Group("/api/v1/teams")
	root.Get("/", s.listTeams)
	root.Post("/", middlewares.RateLimit(s.teamCreateLimit), s.createTeam)
	root.Get("/:team_id", s.getTeam)
	root.Put("/:team_id", s.updateTeam)
	root.Delete("/:team_id", s.deleteTeam)
	root.Post("/:team_id/run", s.runTeam)
}

func (s *Server) listTeams(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	result, err := s.teams.List(c.Context(), principal.UserID)
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, result, "teams retrieved successfully")
}

func (s *Server) createTeam(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	var request teamRequest
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	team, err := s.teams.Create(c.Context(), principal.UserID, request.team())
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusCreated, team, "team created successfully")
}

func (s *Server) getTeam(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	team, err := s.teams.Get(c.Context(), principal.UserID, c.Params("team_id"))
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, team, "team retrieved successfully")
}

func (s *Server) updateTeam(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	var request teamRequest
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	team, err := s.teams.Update(c.Context(), principal.UserID, c.Params("team_id"), request.team())
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, team, "team updated successfully")
}

func (s *Server) deleteTeam(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	if err := s.teams.Delete(c.Context(), principal.UserID, c.Params("team_id")); err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, fiber.Map{"deleted": true}, "team deleted successfully")
}

func (s *Server) runTeam(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	var request struct {
		Task  string `json:"task"`
		Depth int    `json:"depth"`
	}
	if err := bind(c, &request); err != nil {
		return sendError(c, err)
	}
	if strings.TrimSpace(request.Task) == "" {
		return sendError(c, fmt.Errorf("task is required"))
	}
	result, err := s.teams.Run(c.Context(), principal.UserID, c.Params("team_id"), request.Task, request.Depth)
	if err != nil {
		return sendError(c, err)
	}
	return send(c, fiber.StatusOK, result, "team run completed successfully")
}

func (r teamRequest) team() models.Team {
	enabled := true
	if r.Enabled != nil {
		enabled = *r.Enabled
	}
	return models.Team{Name: r.Name, OrchestratorID: r.OrchestratorID, Members: r.Members, SharedWorkspace: r.SharedWorkspace, MaxParallel: r.MaxParallel, MaxDepth: r.MaxDepth, Enabled: enabled}
}

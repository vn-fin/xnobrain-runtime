// <Summary>
// RateLimits binds edition quota decisions to public endpoints without moving enforcement out of services.
// </Summary>
package api

import (
	"strings"

	"github.com/gofiber/fiber/v3"
	"github.com/xno/open-lumora/internal/limits"
)

func (s *Server) agentCreateLimit(c fiber.Ctx) (limits.Decision, error) {
	principal, err := s.principal(c)
	if err != nil {
		return limits.Decision{}, err
	}
	return s.agents.CreateDecision(c.Context(), principal.UserID)
}

func (s *Server) cronCreateLimit(c fiber.Ctx) (limits.Decision, error) {
	principal, err := s.principal(c)
	if err != nil {
		return limits.Decision{}, err
	}
	return s.crons.CreateDecision(c.Context(), principal.UserID)
}

func (s *Server) teamCreateLimit(c fiber.Ctx) (limits.Decision, error) {
	principal, err := s.principal(c)
	if err != nil {
		return limits.Decision{}, err
	}
	return s.teams.CreateDecision(c.Context(), principal.UserID)
}

func (s *Server) cronMonthlyLimit(c fiber.Ctx) (limits.Decision, error) {
	principal, err := s.principal(c)
	if err != nil {
		return limits.Decision{}, err
	}
	return s.crons.MonthlyDecision(c.Context(), principal.UserID)
}

func (s *Server) cronDailyLimit(c fiber.Ctx) (limits.Decision, error) {
	principal, err := s.principal(c)
	if err != nil {
		return limits.Decision{}, err
	}
	return s.crons.DailyDecision(c.Context(), principal.UserID)
}

func (s *Server) cronParallelLimit(c fiber.Ctx) (limits.Decision, error) {
	principal, err := s.principal(c)
	if err != nil {
		return limits.Decision{}, err
	}
	return s.crons.ParallelDecision(c.Context(), principal.UserID)
}

func (s *Server) providerConnectionLimit(c fiber.Ctx) (limits.Decision, error) {
	principal, err := s.principal(c)
	if err != nil {
		return limits.Decision{}, err
	}
	provider, err := validProvider(c.Params("provider_id"))
	if err != nil {
		return limits.Decision{}, err
	}
	plan, err := s.policy.Limits(c.Context(), principal)
	if err != nil {
		return limits.Decision{}, err
	}
	connections, err := s.router.ListConnections(c.Context())
	if err != nil {
		return limits.Decision{}, err
	}
	used := 0
	for _, connection := range connections {
		if strings.EqualFold(connection.Provider, provider) {
			used++
		}
	}
	if shouldReplaceProviderConnection(c) && used > 0 {
		// Replacement collapses every existing connection of this provider to
		// the new one, so it consumes exactly one slot regardless of old count.
		used = 0
	}
	return limits.Check(limits.ProviderConnections, plan.ProviderConnectionsPerType, used, 1, nil), nil
}

func (s *Server) Limits(c fiber.Ctx) error {
	principal, err := s.principal(c)
	if err != nil {
		return sendError(c, err)
	}
	plan, err := s.policy.Limits(c.Context(), principal)
	if err != nil {
		return sendError(c, err)
	}
	agentsDecision, err := s.agents.CreateDecision(c.Context(), principal.UserID)
	if err != nil {
		return sendError(c, err)
	}
	cronDecision, err := s.crons.CreateDecision(c.Context(), principal.UserID)
	if err != nil {
		return sendError(c, err)
	}
	dailyDecision, err := s.crons.DailyDecision(c.Context(), principal.UserID)
	if err != nil {
		return sendError(c, err)
	}
	monthlyDecision, err := s.crons.MonthlyDecision(c.Context(), principal.UserID)
	if err != nil {
		return sendError(c, err)
	}
	parallelDecision, err := s.crons.ParallelDecision(c.Context(), principal.UserID)
	if err != nil {
		return sendError(c, err)
	}
	var teamDecision *limits.Decision
	if s.teams != nil {
		decision, decisionErr := s.teams.CreateDecision(c.Context(), principal.UserID)
		if decisionErr != nil {
			return sendError(c, decisionErr)
		}
		teamDecision = &decision
	}
	usage := fiber.Map{
		string(limits.AgentsCreated):    agentsDecision,
		string(limits.CronJobsCreated):  cronDecision,
		string(limits.CronRunsDaily):    dailyDecision,
		string(limits.CronRunsMonthly):  monthlyDecision,
		string(limits.CronParallelRuns): parallelDecision,
	}
	if teamDecision != nil {
		usage[string(limits.TeamsCreated)] = *teamDecision
	}
	return send(c, fiber.StatusOK, fiber.Map{
		"edition": s.policy.Name(),
		"plan_id": principal.PlanID,
		"limits":  plan,
		"usage":   usage,
	}, "limits retrieved successfully")
}

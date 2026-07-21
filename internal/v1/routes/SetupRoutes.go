// <Summary>
// SetupRoutes is the single route assembly point for every Open Lumora service.
// Add new public HTTP surfaces here after their service and handler contracts exist.
// </Summary>
package routes

import (
	"github.com/gofiber/fiber/v3"
	"github.com/xno/open-lumora/internal/api"
)

func SetupRoutes(app *fiber.App, server *api.Server) {
	app.Get("/api/v1/health", server.Health)
	app.Get("/api/v1/limits", server.Limits)
	app.Get("/api/v1/system/deployment", server.Deployment)
	app.Get("/api/v1/enterprise/features", server.EnterpriseFeatures)
	app.Get("/api/v1/dashboard/overview", server.DashboardOverview)
	app.Get("/api/v1/dashboard/dependencies", server.DashboardDependencies)
	app.Get("/api/v1/observability/traces", server.DashboardTraces)
	app.Get("/api/v1/observability/traces/:trace_id", server.DashboardTrace)
	app.Get("/api/v1/observability/traces/:trace_id/graph", server.DashboardTraceGraph)
	app.Get("/api/v1/observability/metrics", server.DashboardMetrics)
	app.Get("/agent-gateway/v1/ping", server.Health)
	server.RegisterAgentRoutes(app)
	server.RegisterConversationRoutes(app)
	server.RegisterProviderRoutes(app)
	server.RegisterSandboxRoutes(app)
	server.RegisterCronRoutes(app)
	server.RegisterPortabilityRoutes(app)
	server.RegisterDeviceRoutes(app)
	server.RegisterTeamRoutes(app)
	server.RegisterFrontend(app)
}

// <Summary>
// Studio builds the complete Fiber application from public extension contracts.
// Community and enterprise compositions use this same lifecycle and route graph.
// </Summary>
package studio

import (
	"context"
	"fmt"
	"net"
	"strings"
	"sync"
	"time"

	"github.com/gofiber/fiber/v3"
	"github.com/xno/open-lumora/internal/api"
	"github.com/xno/open-lumora/internal/ninerouter"
	"github.com/xno/open-lumora/internal/profile"
	"github.com/xno/open-lumora/internal/repositories"
	runtimeadapter "github.com/xno/open-lumora/internal/runtime"
	v1routes "github.com/xno/open-lumora/internal/v1/routes"
	"github.com/xno/open-lumora/pkg/edition"
	"github.com/xno/open-lumora/pkg/studio/contract"
	"github.com/xno/open-lumora/services/agents"
	"github.com/xno/open-lumora/services/conversations"
	"github.com/xno/open-lumora/services/crons"
	"github.com/xno/open-lumora/services/deviceconnector"
	"github.com/xno/open-lumora/services/portability"
	"github.com/xno/open-lumora/services/teams"
)

type Options struct {
	Config       Config
	Policy       edition.Policy
	Repository   contract.Repository
	Runtime      contract.Runtime
	RuntimeClose func() error
	EventSink    contract.EventSink
	Clock        contract.Clock
	Connector    contract.Connector
}

type Application struct {
	App          *fiber.App
	config       Config
	crons        *crons.Crons
	eventSink    contract.EventSink
	connector    contract.Connector
	runtimeClose func() error
	cancel       context.CancelFunc
	startMu      sync.Mutex
	started      bool
	closeOnce    sync.Once
	closeErr     error
}

type systemClock struct{}

func (systemClock) Now() time.Time { return time.Now().UTC() }

type discardEventSink struct{}

func (discardEventSink) Publish(context.Context, contract.EventEnvelope) error { return nil }

func New(options Options) (*Application, error) {
	if options.Policy == nil {
		return nil, fmt.Errorf("studio policy is required")
	}
	if options.Repository == nil {
		return nil, fmt.Errorf("studio repository is required")
	}
	if options.Runtime == nil {
		return nil, fmt.Errorf("studio runtime is required")
	}
	if strings.TrimSpace(options.Config.DataDir) == "" {
		return nil, fmt.Errorf("studio config DataDir is required")
	}
	if strings.TrimSpace(options.Config.FrontendDir) == "" {
		return nil, fmt.Errorf("studio config FrontendDir is required")
	}
	profiles, err := profile.NewManager(options.Config.DataDir)
	if err != nil {
		return nil, fmt.Errorf("open profiles: %w", err)
	}
	return build(options, profiles)
}

func NewCommunity(config Config) (*Application, error) {
	profiles, err := profile.NewManager(config.DataDir)
	if err != nil {
		return nil, fmt.Errorf("open profiles: %w", err)
	}
	repository, err := repositories.NewFile(profiles)
	if err != nil {
		return nil, fmt.Errorf("open Community repository: %w", err)
	}
	policy := edition.OpenSource{
		AgentLimit: config.MaxOpenSourceAgents, CronJobLimit: config.MaxOpenSourceCronJobs,
		CronConcurrency: config.MaxOpenSourceConcurrency, CronRunsPerDay: config.MaxOpenSourceCronRunsPerDay, CronRunsPerMonth: config.MaxOpenSourceCronRunsPerMonth,
		ProviderConnectionsPerType: config.MaxOpenSourceProviderConnections,
		TeamLimit:                  config.MaxOpenSourceTeams, AgentsPerTeam: config.MaxOpenSourceAgentsPerTeam,
		DelegatedWorkers: config.MaxOpenSourceDelegatedWorkers, DelegationDepth: config.MaxOpenSourceDelegationDepth,
	}
	routerToken, err := ninerouter.New(config.NineRouterURL, config.NineRouterDataDir).APIToken()
	if err != nil {
		return nil, fmt.Errorf("prepare 9router credentials: %w", err)
	}
	var agentRuntime contract.Runtime
	var closeRuntime func() error
	switch {
	case config.HermesRuntimeURL != "":
		gateway := runtimeadapter.NewRemoteGateway(config.HermesRuntimeURL, config.HermesRuntimeToken, config.RuntimeTimeout)
		agentRuntime = gateway
		closeRuntime = func() error { gateway.Close(); return nil }
	case strings.EqualFold(config.HermesRuntimeMode, "gateway"):
		gateway := runtimeadapter.NewGateway(config.HermesGatewayBin, config.RuntimeTimeout, routerToken)
		agentRuntime = gateway
		closeRuntime = func() error { gateway.Close(); return nil }
	default:
		agentRuntime = runtimeadapter.NewCLI(config.HermesBin, config.RuntimeTimeout, routerToken)
		closeRuntime = func() error { return nil }
	}
	return build(Options{Config: config, Policy: policy, Repository: repository, Runtime: agentRuntime, RuntimeClose: closeRuntime}, profiles)
}

func build(options Options, profiles *profile.Manager) (*Application, error) {
	if options.EventSink == nil {
		options.EventSink = discardEventSink{}
	}
	if options.Clock == nil {
		options.Clock = systemClock{}
	}
	if options.RuntimeClose == nil {
		options.RuntimeClose = func() error { return nil }
	}
	agentService := agents.NewAgents(options.Repository, profiles, options.Policy)
	conversationService := conversations.NewConversations(options.Repository, agentService, options.Runtime)
	cronService := crons.NewCrons(options.Repository, agentService, conversationService, options.Policy, options.Clock)
	if options.Connector == nil && strings.TrimSpace(options.Config.DeviceCloudURL) != "" {
		controlPlane, err := deviceconnector.NewHTTPControlPlane(options.Config.DeviceCloudURL, options.Config.RuntimeTimeout)
		if err != nil {
			return nil, fmt.Errorf("configure device connector: %w", err)
		}
		notificationStore, _ := options.Repository.(contract.NotificationStore)
		dispatcher := deviceconnector.NewRuntimeDispatcher("local", cronService, notificationStore)
		options.Connector, err = deviceconnector.NewConnector(deviceconnector.Config{DataDir: options.Config.DataDir, Endpoint: options.Config.DeviceCloudURL, ServerSigningPublicKey: options.Config.DeviceSigningPublicKey, PollInterval: options.Config.DevicePollInterval}, controlPlane, dispatcher, dispatcher)
		if err != nil {
			return nil, fmt.Errorf("open device connector: %w", err)
		}
	}
	internalConfig := options.Config.internal()
	server := api.NewServer(internalConfig, options.Policy, agentService, conversationService, cronService)
	teamStore, hasTeamStore := options.Repository.(contract.TeamStore)
	if hasTeamStore {
		server.SetPortability(portability.NewPortability(agentService, profiles, teamStore))
		server.SetTeams(teams.NewTeams(teamStore, agentService, teams.NewConversationsDelegator(conversationService), options.Policy))
	} else {
		server.SetPortability(portability.NewPortability(agentService, profiles))
	}
	server.SetConnector(options.Connector)
	app := api.NewApp(internalConfig, server)
	v1routes.SetupRoutes(app, server)
	return &Application{App: app, config: options.Config, crons: cronService, eventSink: options.EventSink, connector: options.Connector, runtimeClose: options.RuntimeClose}, nil
}

func (a *Application) Listen(ctx context.Context) error {
	listener, err := net.Listen("tcp", fmt.Sprintf("0.0.0.0:%d", a.config.HTTPPort))
	if err != nil {
		return err
	}
	return a.Serve(ctx, listener)
}

func (a *Application) Serve(ctx context.Context, listener net.Listener) error {
	if listener == nil {
		return fmt.Errorf("studio listener is required")
	}
	a.startMu.Lock()
	if a.started {
		a.startMu.Unlock()
		return fmt.Errorf("studio application already started")
	}
	a.started = true
	runContext, cancel := context.WithCancel(ctx)
	a.cancel = cancel
	a.startMu.Unlock()
	go a.crons.Run(runContext)
	if a.connector != nil {
		go func() { _ = a.connector.Start(runContext) }()
	}
	_ = a.eventSink.Publish(runContext, contract.EventEnvelope{Type: "service.started", OccurredAt: time.Now().UTC(), Attributes: map[string]any{"edition": a.config.Edition}})
	return a.App.Listener(listener, fiber.ListenConfig{GracefulContext: runContext})
}

func (a *Application) Close() error {
	a.closeOnce.Do(func() {
		if a.cancel != nil {
			a.cancel()
		}
		if a.connector != nil {
			a.closeErr = a.connector.Close()
		}
		if err := a.runtimeClose(); a.closeErr == nil {
			a.closeErr = err
		}
	})
	return a.closeErr
}

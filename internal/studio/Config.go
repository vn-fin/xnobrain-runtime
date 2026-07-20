// <Summary>
// Config is the public process configuration accepted by the Studio builder.
// LoadConfig reads the Community environment without exposing internal packages.
// </Summary>
package studio

import (
	"time"

	internalconfig "github.com/xno/open-lumora/internal/config"
)

type Config struct {
	StartMode            string
	Edition              string
	HTTPPort             int
	LogLevel             string
	CORSAllowedOrigins   []string
	DataDir              string
	FrontendDir          string
	HermesBin            string
	HermesGatewayBin     string
	HermesRuntimeMode    string
	HermesRuntimeURL     string
	HermesRuntimeToken   string
	ControlGatewayURL    string
	NineRouterURL        string
	NineRouterDataDir    string
	RuntimeTimeout       time.Duration
	ContainerIdleEnabled bool
	ContainerIdleTimeout time.Duration
	// Deprecated compatibility fields. Self-hosted composition ignores these
	// values and always grants unrestricted local Hermes access.
	MaxOpenSourceAgents              int
	MaxOpenSourceCronJobs            int
	MaxOpenSourceConcurrency         int
	MaxOpenSourceCronRunsPerDay      int
	MaxOpenSourceCronRunsPerMonth    int
	MaxOpenSourceProviderConnections int
	PermissionGRPCHost               string
	AuthTimeout                      time.Duration
	OTLPEndpoint                     string
	DeviceCloudURL                   string
	DeviceSigningPublicKey           string
	DevicePollInterval               time.Duration
	TelemetryDiskCapBytes            int64
	MaxOpenSourceTeams               int
	MaxOpenSourceAgentsPerTeam       int
	MaxOpenSourceDelegatedWorkers    int
	MaxOpenSourceDelegationDepth     int
	ManagedWorkerToken               string
	SchedulerEnabled                 bool
}

func LoadConfig() (Config, error) {
	loaded, err := internalconfig.Load()
	if err != nil {
		return Config{}, err
	}
	return configFromInternal(loaded), nil
}

func configFromInternal(config internalconfig.Config) Config {
	return Config{
		StartMode: config.StartMode, Edition: config.Edition, HTTPPort: config.HTTPPort, LogLevel: config.LogLevel,
		CORSAllowedOrigins: config.CORSAllowedOrigins, DataDir: config.DataDir, FrontendDir: config.FrontendDir,
		HermesBin: config.HermesBin, HermesGatewayBin: config.HermesGatewayBin, HermesRuntimeMode: config.HermesRuntimeMode,
		HermesRuntimeURL: config.HermesRuntimeURL, HermesRuntimeToken: config.HermesRuntimeToken, ControlGatewayURL: config.ControlGatewayURL,
		NineRouterURL: config.NineRouterURL, NineRouterDataDir: config.NineRouterDataDir, RuntimeTimeout: config.RuntimeTimeout,
		ContainerIdleEnabled: config.ContainerIdleEnabled, ContainerIdleTimeout: config.ContainerIdleTimeout,
		PermissionGRPCHost: config.PermissionGRPCHost, AuthTimeout: config.AuthTimeout, OTLPEndpoint: config.OTLPEndpoint,
		DeviceCloudURL: config.DeviceCloudURL, DeviceSigningPublicKey: config.DeviceSigningPublicKey, DevicePollInterval: config.DevicePollInterval,
		TelemetryDiskCapBytes: config.TelemetryDiskCapBytes,
		ManagedWorkerToken:    config.ManagedWorkerToken, SchedulerEnabled: config.SchedulerEnabled,
	}
}

func (c Config) internal() internalconfig.Config {
	return internalconfig.Config{
		StartMode: c.StartMode, Edition: c.Edition, HTTPPort: c.HTTPPort, LogLevel: c.LogLevel,
		CORSAllowedOrigins: c.CORSAllowedOrigins, DataDir: c.DataDir, FrontendDir: c.FrontendDir,
		HermesBin: c.HermesBin, HermesGatewayBin: c.HermesGatewayBin, HermesRuntimeMode: c.HermesRuntimeMode,
		HermesRuntimeURL: c.HermesRuntimeURL, HermesRuntimeToken: c.HermesRuntimeToken, ControlGatewayURL: c.ControlGatewayURL,
		NineRouterURL: c.NineRouterURL, NineRouterDataDir: c.NineRouterDataDir, RuntimeTimeout: c.RuntimeTimeout,
		ContainerIdleEnabled: c.ContainerIdleEnabled, ContainerIdleTimeout: c.ContainerIdleTimeout,
		PermissionGRPCHost: c.PermissionGRPCHost, AuthTimeout: c.AuthTimeout, OTLPEndpoint: c.OTLPEndpoint,
		DeviceCloudURL: c.DeviceCloudURL, DeviceSigningPublicKey: c.DeviceSigningPublicKey, DevicePollInterval: c.DevicePollInterval,
		TelemetryDiskCapBytes: c.TelemetryDiskCapBytes,
		ManagedWorkerToken:    c.ManagedWorkerToken, SchedulerEnabled: c.SchedulerEnabled,
	}
}

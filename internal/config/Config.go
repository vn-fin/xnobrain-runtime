// <Summary>
// Package config loads environment-backed process configuration.
// File: Config.go
// Functions:
//   - Load() (Config, error)
//
// </Summary>
package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/joho/godotenv"
)

type Config struct {
	Edition                          string
	HTTPPort                         int
	LogLevel                         string
	CORSAllowedOrigins               []string
	DataDir                          string
	FrontendDir                      string
	HermesBin                        string
	HermesGatewayBin                 string
	HermesRuntimeMode                string
	HermesRuntimeURL                 string
	HermesRuntimeToken               string
	NineRouterURL                    string
	NineRouterDataDir                string
	RuntimeTimeout                   time.Duration
	ContainerIdleEnabled             bool
	ContainerIdleTimeout             time.Duration
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
}

func Load() (Config, error) {
	_ = godotenv.Load(".env")
	dataDir, err := filepath.Abs(value("DATA_DIR", "data"))
	if err != nil {
		return Config{}, fmt.Errorf("resolve DATA_DIR: %w", err)
	}
	frontendDir, err := filepath.Abs(value("FRONTEND_DIR", "frontend/dist"))
	if err != nil {
		return Config{}, fmt.Errorf("resolve FRONTEND_DIR: %w", err)
	}
	nineRouterDataDir, err := filepath.Abs(value("NINE_ROUTER_DATA_DIR", filepath.Join(dataDir, "root", ".9router")))
	if err != nil {
		return Config{}, fmt.Errorf("resolve NINE_ROUTER_DATA_DIR: %w", err)
	}

	return Config{
		Edition:                          value("OPEN_LUMORA_EDITION", "opensource"),
		HTTPPort:                         integer("HTTP_PORT", 3000),
		LogLevel:                         value("LOG_LEVEL", "debug"),
		CORSAllowedOrigins:               list("CORS_ALLOWED_ORIGINS", []string{"http://localhost:5173", "http://localhost:3000"}),
		DataDir:                          dataDir,
		FrontendDir:                      frontendDir,
		HermesBin:                        value("HERMES_BIN", "hermes"),
		HermesGatewayBin:                 value("HERMES_GATEWAY_BIN", "hermes-custom-gateway"),
		HermesRuntimeMode:                value("HERMES_RUNTIME_MODE", "cli"),
		HermesRuntimeURL:                 strings.TrimRight(strings.TrimSpace(os.Getenv("HERMES_RUNTIME_URL")), "/"),
		HermesRuntimeToken:               strings.TrimSpace(os.Getenv("HERMES_RUNTIME_TOKEN")),
		NineRouterURL:                    value("NINE_ROUTER_URL", "http://127.0.0.1:20128"),
		NineRouterDataDir:                nineRouterDataDir,
		RuntimeTimeout:                   time.Duration(integer("RUNTIME_TIMEOUT_SECONDS", 900)) * time.Second,
		ContainerIdleEnabled:             boolean("CONTAINER_IDLE_ENABLED", true),
		ContainerIdleTimeout:             time.Duration(integer("CONTAINER_IDLE_TIMEOUT_MINUTES", 30)) * time.Minute,
		MaxOpenSourceAgents:              integer("MAX_OPEN_SOURCE_AGENTS", 4),
		MaxOpenSourceCronJobs:            integer("MAX_OPEN_SOURCE_CRON_JOBS", 4),
		MaxOpenSourceConcurrency:         integer("MAX_OPEN_SOURCE_CRON_CONCURRENCY", 1),
		MaxOpenSourceCronRunsPerDay:      integer("MAX_OPEN_SOURCE_CRON_RUNS_PER_DAY", 10),
		MaxOpenSourceCronRunsPerMonth:    integer("MAX_OPEN_SOURCE_CRON_RUNS_PER_MONTH", 200),
		MaxOpenSourceProviderConnections: integer("MAX_OPEN_SOURCE_PROVIDER_CONNECTIONS_PER_TYPE", 1),
		PermissionGRPCHost:               strings.TrimSpace(os.Getenv("PERMISSION_GRPC_HOST")),
		AuthTimeout:                      time.Duration(integer("AUTH_TIMEOUT_SECONDS", 1)) * time.Second,
		OTLPEndpoint:                     strings.TrimSpace(os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT")),
		DeviceCloudURL:                   strings.TrimRight(strings.TrimSpace(os.Getenv("DEVICE_CLOUD_URL")), "/"),
		DeviceSigningPublicKey:           strings.TrimSpace(os.Getenv("DEVICE_SIGNING_PUBLIC_KEY")),
		DevicePollInterval:               time.Duration(integer("DEVICE_POLL_INTERVAL_SECONDS", 20)) * time.Second,
		TelemetryDiskCapBytes:            int64(integer("OTEL_DISK_BUFFER_BYTES", 64*1024*1024)),
		MaxOpenSourceTeams:               integer("MAX_OPEN_SOURCE_TEAMS", 1),
		MaxOpenSourceAgentsPerTeam:       integer("MAX_OPEN_SOURCE_AGENTS_PER_TEAM", 2),
		MaxOpenSourceDelegatedWorkers:    integer("MAX_OPEN_SOURCE_DELEGATED_WORKERS", 1),
		MaxOpenSourceDelegationDepth:     integer("MAX_OPEN_SOURCE_DELEGATION_DEPTH", 1),
		ManagedWorkerToken:               strings.TrimSpace(os.Getenv("MANAGED_WORKER_TOKEN")),
	}, nil
}

func value(key string, fallback string) string {
	if current := strings.TrimSpace(os.Getenv(key)); current != "" {
		return current
	}
	return fallback
}

func integer(key string, fallback int) int {
	parsed, err := strconv.Atoi(strings.TrimSpace(os.Getenv(key)))
	if err != nil || parsed <= 0 {
		return fallback
	}
	return parsed
}

func boolean(key string, fallback bool) bool {
	current := strings.TrimSpace(os.Getenv(key))
	if current == "" {
		return fallback
	}
	parsed, err := strconv.ParseBool(current)
	if err != nil {
		return fallback
	}
	return parsed
}

func list(key string, fallback []string) []string {
	current := strings.TrimSpace(os.Getenv(key))
	if current == "" {
		return fallback
	}
	result := make([]string, 0)
	for _, item := range strings.Split(current, ",") {
		if item = strings.TrimSpace(item); item != "" {
			result = append(result, item)
		}
	}
	return result
}

// <Summary>
// Package main wires and starts the Open Lumora monolith.
// File: main.go
// Functions:
//   - main
//   - run() error
//
// </Summary>
package main

import (
	"context"
	"os"
	"os/signal"
	"syscall"

	"github.com/rs/zerolog/log"
	"github.com/xno/open-lumora/internal/logger"
	"github.com/xno/open-lumora/internal/tracing"
	"github.com/xno/open-lumora/pkg/studio"
)

func main() {
	if err := run(); err != nil {
		log.Fatal().Err(err).Msg("open lumora stopped")
	}
}

func run() error {
	cfg, err := studio.LoadConfig()
	if err != nil {
		return err
	}
	closeLogger := logger.SetupLogger("open-lumora", cfg.LogLevel, cfg.DataDir)
	defer closeLogger()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	cleanupTracing, err := tracing.Setup(ctx, cfg.OTLPEndpoint, cfg.DataDir, cfg.TelemetryDiskCapBytes)
	if err != nil {
		return err
	}
	defer cleanupTracing()
	application, err := studio.NewCommunity(cfg)
	if err != nil {
		return err
	}
	defer application.Close()
	log.Info().Int("port", cfg.HTTPPort).Str("edition", cfg.Edition).Msg("open lumora starting")
	return application.Listen(ctx)
}

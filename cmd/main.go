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
	"github.com/xno/open-lumora/internal/studio"
	"github.com/xno/open-lumora/internal/tracing"
)

func main() {
	if len(os.Args) == 2 && os.Args[1] == "scheduler-worker" {
		if err := runProcess(true); err != nil {
			log.Fatal().Err(err).Msg("open lumora scheduler stopped")
		}
		return
	}
	if err := run(); err != nil {
		log.Fatal().Err(err).Msg("open lumora stopped")
	}
}

func run() error {
	return runProcess(false)
}

func runProcess(schedulerOnly bool) error {
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
	if schedulerOnly {
		log.Info().Str("start_mode", cfg.StartMode).Msg("local schedule listener started")
		application.RunScheduler(ctx)
		return nil
	}
	log.Info().Int("port", cfg.HTTPPort).Str("edition", cfg.Edition).Str("start_mode", cfg.StartMode).Msg("open lumora starting")
	return application.Listen(ctx)
}

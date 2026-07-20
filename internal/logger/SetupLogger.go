// <Summary>
// SetupLogger configures the process-wide structured logger used by HTTP, services, and runtime adapters.
// </Summary>
package logger

import (
	"os"
	"path/filepath"

	"github.com/rs/zerolog"
	"github.com/rs/zerolog/log"
)

func SetupLogger(serviceName string, configuredLevel string, dataDirs ...string) func() {
	level, err := zerolog.ParseLevel(configuredLevel)
	if err != nil {
		level = zerolog.InfoLevel
	}
	zerolog.SetGlobalLevel(level)
	writer := zerolog.LevelWriter(zerolog.MultiLevelWriter(os.Stdout))
	var local *dailyWriter
	if len(dataDirs) > 0 && dataDirs[0] != "" {
		if opened, openErr := newDailyWriter(filepath.Join(dataDirs[0], "logs"), 7); openErr == nil {
			local = opened
			writer = zerolog.MultiLevelWriter(os.Stdout, local)
		}
	}
	log.Logger = zerolog.New(writer).With().
		Timestamp().
		Str("service_name", serviceName).
		Logger()
	return func() {
		if local != nil {
			_ = local.Close()
		}
	}
}

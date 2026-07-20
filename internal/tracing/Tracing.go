// <Summary>
// Package tracing configures OpenTelemetry tracing with optional OTLP export.
// File: Tracing.go
// Functions:
//   - Setup(ctx context.Context, endpoint string) (func(), error)
//
// </Summary>
package tracing

import (
	"context"
	"crypto/tls"
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/sdk/resource"
	"go.opentelemetry.io/otel/sdk/trace"
	"google.golang.org/grpc/credentials"
)

func Setup(ctx context.Context, endpoint string, dataDir string, diskCapBytes int64) (func(), error) {
	options := make([]trace.TracerProviderOption, 0)
	if strings.TrimSpace(endpoint) != "" {
		address, insecure, err := collectorAddress(endpoint)
		if err != nil {
			return nil, err
		}
		exportCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
		defer cancel()
		exporterOptions := []otlptracegrpc.Option{otlptracegrpc.WithEndpoint(address)}
		if insecure {
			exporterOptions = append(exporterOptions, otlptracegrpc.WithInsecure())
		} else {
			exporterOptions = append(exporterOptions, otlptracegrpc.WithTLSCredentials(credentials.NewTLS(&tls.Config{MinVersion: tls.VersionTLS12})))
		}
		if headers := otlpHeaders(os.Getenv("OTEL_EXPORTER_OTLP_HEADERS")); len(headers) > 0 {
			exporterOptions = append(exporterOptions, otlptracegrpc.WithHeaders(headers))
		}
		exporter, err := otlptracegrpc.New(exportCtx, exporterOptions...)
		if err != nil {
			return nil, err
		}
		var durable trace.SpanExporter = exporter
		if strings.TrimSpace(dataDir) != "" && diskCapBytes > 0 {
			spool, err := NewSpoolExporter(exporter, filepath.Join(dataDir, "telemetry"), diskCapBytes)
			if err != nil {
				return nil, err
			}
			durable = spool
		}
		options = append(options, trace.WithBatcher(NewSafeExporter(durable), trace.WithMaxQueueSize(2048), trace.WithMaxExportBatchSize(256), trace.WithBatchTimeout(2*time.Second), trace.WithExportTimeout(5*time.Second)))
	}
	serviceResource, err := resource.New(ctx, resource.WithAttributes(
		attribute.String("service.name", "open-lumora"),
		attribute.String("service.namespace", "xno"),
	))
	if err != nil {
		return nil, err
	}
	options = append(options, trace.WithResource(serviceResource))
	provider := trace.NewTracerProvider(options...)
	otel.SetTracerProvider(provider)
	return func() {
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = provider.Shutdown(shutdownCtx)
	}, nil
}

func collectorAddress(endpoint string) (string, bool, error) {
	endpoint = strings.TrimSpace(endpoint)
	if !strings.Contains(endpoint, "://") {
		host, _, err := net.SplitHostPort(endpoint)
		if err != nil {
			return "", false, fmt.Errorf("OTLP endpoint must be host:port or an HTTPS URL")
		}
		return endpoint, isLocalCollector(host), nil
	}
	parsed, err := url.Parse(endpoint)
	if err != nil || parsed.Host == "" || parsed.Path != "" && parsed.Path != "/" {
		return "", false, fmt.Errorf("invalid OTLP endpoint")
	}
	if parsed.Scheme == "https" {
		return parsed.Host, false, nil
	}
	if parsed.Scheme == "http" && isLocalCollector(parsed.Hostname()) {
		return parsed.Host, true, nil
	}
	return "", false, fmt.Errorf("remote OTLP endpoint must use HTTPS")
}

func isLocalCollector(host string) bool {
	return strings.EqualFold(host, "localhost") || net.ParseIP(host) != nil && net.ParseIP(host).IsLoopback()
}

func otlpHeaders(raw string) map[string]string {
	result := make(map[string]string)
	for _, item := range strings.Split(raw, ",") {
		parts := strings.SplitN(strings.TrimSpace(item), "=", 2)
		if len(parts) != 2 || strings.TrimSpace(parts[0]) == "" {
			continue
		}
		value, err := url.QueryUnescape(strings.TrimSpace(parts[1]))
		if err == nil {
			result[strings.TrimSpace(parts[0])] = value
		}
	}
	return result
}

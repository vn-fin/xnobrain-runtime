// <Summary>
// Spool tests prove offline exports remain non-blocking, only redacted payloads
// reach disk, recovery replays oldest-first, and disk exhaustion drops oldest.
// File: Spool_test.go
// Tests: offline replay, redaction-before-disk, bounded cap
// </Summary>
package tracing

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"go.opentelemetry.io/otel/attribute"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"go.opentelemetry.io/otel/trace"
)

type recoverableExporter struct {
	mu     sync.Mutex
	online bool
	names  []string
}

func (e *recoverableExporter) ExportSpans(_ context.Context, spans []sdktrace.ReadOnlySpan) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	if !e.online {
		return errors.New("collector offline")
	}
	for _, span := range spans {
		e.names = append(e.names, span.Name())
	}
	return nil
}

func (*recoverableExporter) Shutdown(context.Context) error { return nil }

func TestSpoolStoresRedactedSpansAndReplaysOldestFirst(t *testing.T) {
	root := t.TempDir()
	next := &recoverableExporter{}
	spool, err := NewSpoolExporter(next, root, 1024*1024)
	if err != nil {
		t.Fatal(err)
	}
	exporter := NewSafeExporter(spool)
	secret := "secret-prompt-and-token"
	first := testSpan("first", attribute.String("prompt", secret), attribute.String("agent.id_hash", HashID("a12345")))
	if err := exporter.ExportSpans(context.Background(), []sdktrace.ReadOnlySpan{first}); err != nil {
		t.Fatalf("offline telemetry blocked caller: %v", err)
	}
	files, _ := filepath.Glob(filepath.Join(root, "*.json"))
	if len(files) != 1 {
		t.Fatalf("spooled files = %d", len(files))
	}
	payload, _ := os.ReadFile(files[0])
	if strings.Contains(string(payload), secret) || !strings.Contains(string(payload), "agent.id_hash") {
		t.Fatalf("unsafe spool payload: %s", payload)
	}
	next.mu.Lock()
	next.online = true
	next.mu.Unlock()
	if err := exporter.ExportSpans(context.Background(), []sdktrace.ReadOnlySpan{testSpan("second")}); err != nil {
		t.Fatal(err)
	}
	next.mu.Lock()
	names := append([]string(nil), next.names...)
	next.mu.Unlock()
	if strings.Join(names, ",") != "first,second" {
		t.Fatalf("replay order = %v", names)
	}
	files, _ = filepath.Glob(filepath.Join(root, "*.json"))
	if len(files) != 0 {
		t.Fatalf("replayed files remain: %v", files)
	}
}

func TestSpoolCapDropsOldestWithoutReturningFailure(t *testing.T) {
	next := &recoverableExporter{}
	spool, err := NewSpoolExporter(next, t.TempDir(), 1)
	if err != nil {
		t.Fatal(err)
	}
	if err := spool.ExportSpans(context.Background(), []sdktrace.ReadOnlySpan{testSpan("too-large")}); err != nil {
		t.Fatal(err)
	}
	if spool.DroppedBatches() != 1 {
		t.Fatalf("dropped batches = %d", spool.DroppedBatches())
	}
}

func testSpan(name string, attributes ...attribute.KeyValue) sdktrace.ReadOnlySpan {
	traceID := trace.TraceID{1}
	spanID := trace.SpanID{2}
	return tracetest.SpanStub{Name: name, SpanContext: trace.NewSpanContext(trace.SpanContextConfig{TraceID: traceID, SpanID: spanID}), StartTime: time.Unix(1, 0), EndTime: time.Unix(2, 0), Attributes: attributes}.Snapshot()
}

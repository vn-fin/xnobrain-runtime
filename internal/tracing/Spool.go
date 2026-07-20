// <Summary>
// SpoolExporter persists already-redacted spans when OTLP is unavailable,
// enforces a byte cap by dropping oldest batches, and replays before new spans.
// File: Spool.go
// Types: SpoolExporter, portableSpan
// </Summary>
package tracing

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/sdk/instrumentation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

type portableSpan struct {
	Name       string         `json:"name"`
	TraceID    string         `json:"trace_id"`
	SpanID     string         `json:"span_id"`
	ParentID   string         `json:"parent_id,omitempty"`
	TraceFlags byte           `json:"trace_flags"`
	Kind       int            `json:"kind"`
	Start      time.Time      `json:"start"`
	End        time.Time      `json:"end"`
	Attributes map[string]any `json:"attributes,omitempty"`
	Status     int            `json:"status"`
	Resource   map[string]any `json:"resource,omitempty"`
	ScopeName  string         `json:"scope_name,omitempty"`
	ScopeVer   string         `json:"scope_version,omitempty"`
}

type SpoolExporter struct {
	next           sdktrace.SpanExporter
	root           string
	maxBytes       int64
	mu             sync.Mutex
	sequence       uint64
	droppedBatches atomic.Uint64
}

func NewSpoolExporter(next sdktrace.SpanExporter, root string, maxBytes int64) (*SpoolExporter, error) {
	if next == nil || maxBytes < 1 {
		return nil, fmt.Errorf("telemetry exporter and positive disk cap are required")
	}
	if err := os.MkdirAll(root, 0o700); err != nil {
		return nil, err
	}
	return &SpoolExporter{next: next, root: root, maxBytes: maxBytes}, nil
}

func (e *SpoolExporter) ExportSpans(ctx context.Context, spans []sdktrace.ReadOnlySpan) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	if err := e.flush(ctx); err != nil {
		return e.persist(spans)
	}
	if err := e.next.ExportSpans(ctx, spans); err != nil {
		return e.persist(spans)
	}
	return nil
}

func (e *SpoolExporter) Shutdown(ctx context.Context) error {
	e.mu.Lock()
	_ = e.flush(ctx)
	e.mu.Unlock()
	return e.next.Shutdown(ctx)
}

func (e *SpoolExporter) DroppedBatches() uint64 { return e.droppedBatches.Load() }

func (e *SpoolExporter) flush(ctx context.Context) error {
	files, err := e.files()
	if err != nil {
		return err
	}
	for _, name := range files {
		payload, err := os.ReadFile(name)
		if err != nil {
			return err
		}
		var stored []portableSpan
		if err := json.Unmarshal(payload, &stored); err != nil {
			return err
		}
		spans, err := restoreSpans(stored)
		if err != nil {
			return err
		}
		if err := e.next.ExportSpans(ctx, spans); err != nil {
			return err
		}
		if err := os.Remove(name); err != nil {
			return err
		}
	}
	return syncDirectory(e.root)
}

func (e *SpoolExporter) persist(spans []sdktrace.ReadOnlySpan) error {
	if len(spans) == 0 {
		return nil
	}
	stored := make([]portableSpan, 0, len(spans))
	for _, span := range spans {
		stored = append(stored, storeSpan(span))
	}
	payload, err := json.Marshal(stored)
	if err != nil {
		return nil
	}
	e.sequence++
	name := filepath.Join(e.root, fmt.Sprintf("%020d-%06d.json", time.Now().UTC().UnixNano(), e.sequence))
	if err := atomicWrite(name, append(payload, '\n')); err != nil {
		return nil
	}
	_ = e.enforceCap()
	return nil
}

func (e *SpoolExporter) enforceCap() error {
	files, err := e.files()
	if err != nil {
		return err
	}
	var total int64
	sizes := make(map[string]int64, len(files))
	for _, name := range files {
		info, err := os.Stat(name)
		if err != nil {
			return err
		}
		sizes[name] = info.Size()
		total += info.Size()
	}
	for _, name := range files {
		if total <= e.maxBytes {
			break
		}
		if err := os.Remove(name); err != nil {
			return err
		}
		total -= sizes[name]
		e.droppedBatches.Add(1)
	}
	return syncDirectory(e.root)
}

func (e *SpoolExporter) files() ([]string, error) {
	entries, err := os.ReadDir(e.root)
	if err != nil {
		return nil, err
	}
	files := make([]string, 0, len(entries))
	for _, entry := range entries {
		if !entry.IsDir() && filepath.Ext(entry.Name()) == ".json" {
			files = append(files, filepath.Join(e.root, entry.Name()))
		}
	}
	sort.Strings(files)
	return files, nil
}

func storeSpan(span sdktrace.ReadOnlySpan) portableSpan {
	result := portableSpan{Name: span.Name(), TraceID: span.SpanContext().TraceID().String(), SpanID: span.SpanContext().SpanID().String(), ParentID: span.Parent().SpanID().String(), TraceFlags: byte(span.SpanContext().TraceFlags()), Kind: int(span.SpanKind()), Start: span.StartTime(), End: span.EndTime(), Attributes: values(span.Attributes()), Status: int(span.Status().Code), ScopeName: span.InstrumentationScope().Name, ScopeVer: span.InstrumentationScope().Version}
	if span.Resource() != nil {
		result.Resource = values(span.Resource().Attributes())
	}
	return result
}

func values(items []attribute.KeyValue) map[string]any {
	result := make(map[string]any, len(items))
	for _, item := range items {
		result[string(item.Key)] = item.Value.AsInterface()
	}
	return result
}

func restoreSpans(stored []portableSpan) ([]sdktrace.ReadOnlySpan, error) {
	result := make([]sdktrace.ReadOnlySpan, 0, len(stored))
	for _, item := range stored {
		span, err := item.restore()
		if err != nil {
			return nil, err
		}
		result = append(result, span)
	}
	return result, nil
}

func (p portableSpan) restore() (sdktrace.ReadOnlySpan, error) {
	traceID, err := traceID(p.TraceID)
	if err != nil {
		return nil, err
	}
	parsedSpanID, err := spanID(p.SpanID)
	if err != nil {
		return nil, err
	}
	parentID, _ := spanID(p.ParentID)
	return replaySpan{traceID: traceID, spanID: parsedSpanID, parentID: parentID, data: p}, nil
}

type replaySpan struct {
	sdktrace.ReadOnlySpan
	traceID  trace.TraceID
	spanID   trace.SpanID
	parentID trace.SpanID
	data     portableSpan
}

func (s replaySpan) Name() string { return s.data.Name }
func (s replaySpan) SpanContext() trace.SpanContext {
	return trace.NewSpanContext(trace.SpanContextConfig{TraceID: s.traceID, SpanID: s.spanID, TraceFlags: trace.TraceFlags(s.data.TraceFlags)})
}
func (s replaySpan) Parent() trace.SpanContext {
	return trace.NewSpanContext(trace.SpanContextConfig{TraceID: s.traceID, SpanID: s.parentID})
}
func (s replaySpan) SpanKind() trace.SpanKind         { return trace.SpanKind(s.data.Kind) }
func (s replaySpan) StartTime() time.Time             { return s.data.Start }
func (s replaySpan) EndTime() time.Time               { return s.data.End }
func (s replaySpan) Attributes() []attribute.KeyValue { return attributes(s.data.Attributes) }
func (s replaySpan) Links() []sdktrace.Link           { return nil }
func (s replaySpan) Events() []sdktrace.Event         { return nil }
func (s replaySpan) Status() sdktrace.Status          { return sdktrace.Status{Code: codes.Code(s.data.Status)} }
func (s replaySpan) InstrumentationScope() instrumentation.Scope {
	return instrumentation.Scope{Name: s.data.ScopeName, Version: s.data.ScopeVer}
}
func (s replaySpan) InstrumentationLibrary() instrumentation.Library { return s.InstrumentationScope() }
func (s replaySpan) Resource() *resource.Resource {
	return resource.NewSchemaless(attributes(s.data.Resource)...)
}
func (s replaySpan) DroppedAttributes() int { return 0 }
func (s replaySpan) DroppedLinks() int      { return 0 }
func (s replaySpan) DroppedEvents() int     { return 0 }
func (s replaySpan) ChildSpanCount() int    { return 0 }

func attributes(values map[string]any) []attribute.KeyValue {
	result := make([]attribute.KeyValue, 0, len(values))
	for key, value := range values {
		switch current := value.(type) {
		case string:
			result = append(result, attribute.String(key, current))
		case bool:
			result = append(result, attribute.Bool(key, current))
		case float64:
			result = append(result, attribute.Float64(key, current))
		}
	}
	sort.Slice(result, func(left, right int) bool { return result[left].Key < result[right].Key })
	return result
}

func traceID(value string) (trace.TraceID, error) {
	decoded, err := hex.DecodeString(value)
	if err != nil || len(decoded) != 16 {
		return trace.TraceID{}, errors.New("invalid trace id")
	}
	var result trace.TraceID
	copy(result[:], decoded)
	return result, nil
}

func spanID(value string) (trace.SpanID, error) {
	if value == "" || value == "0000000000000000" {
		return trace.SpanID{}, nil
	}
	decoded, err := hex.DecodeString(value)
	if err != nil || len(decoded) != 8 {
		return trace.SpanID{}, errors.New("invalid span id")
	}
	var result trace.SpanID
	copy(result[:], decoded)
	return result, nil
}

func atomicWrite(name string, payload []byte) error {
	file, err := os.CreateTemp(filepath.Dir(name), ".telemetry-*")
	if err != nil {
		return err
	}
	temporary := file.Name()
	defer os.Remove(temporary)
	if err := file.Chmod(0o600); err != nil {
		_ = file.Close()
		return err
	}
	if _, err := file.Write(payload); err != nil {
		_ = file.Close()
		return err
	}
	if err := file.Sync(); err != nil {
		_ = file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return os.Rename(temporary, name)
}

func syncDirectory(name string) error {
	directory, err := os.Open(name)
	if err != nil {
		return err
	}
	defer directory.Close()
	return directory.Sync()
}

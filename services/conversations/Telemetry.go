// <Summary>
// Telemetry converts safe Hermes run events into OpenTelemetry usage and
// dependency spans without retaining prompts, responses, or tool arguments.
// </Summary>
package conversations

import (
	"context"
	"fmt"
	"strings"
	"time"
	"unicode/utf8"

	runtimeadapter "github.com/xno/open-lumora/internal/runtime"
	safetracing "github.com/xno/open-lumora/internal/tracing"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"
)

type runTelemetry struct {
	agentID          string
	agentName        string
	provider         string
	model            string
	inputTokens      int64
	outputTokens     int64
	cachedTokens     int64
	cacheReadTokens  int64
	cacheWriteTokens int64
	reasoningTokens  int64
	costUSD          float64
	seen             map[string]struct{}
	active           map[string]trace.Span
	startedAt        time.Time
	firstOutputAt    time.Time
	streamEvents     int64
}

func newRunTelemetry(agentID string) *runTelemetry {
	return &runTelemetry{agentID: safetracing.HashID(agentID), seen: make(map[string]struct{}), active: make(map[string]trace.Span), startedAt: time.Now()}
}

func (m *runTelemetry) Configure(agentName, provider, model string) {
	m.agentName = strings.TrimSpace(agentName)
	m.provider = strings.TrimSpace(provider)
	m.model = strings.TrimSpace(model)
}

func (m *runTelemetry) Observe(ctx context.Context, event runtimeadapter.Event) {
	payload := event.Payload
	if payload == nil {
		return
	}
	if value := nestedText(payload, "provider", "provider_name"); value != "" {
		m.provider = value
	}
	if value := nestedText(payload, "response_model", "model", "model_name"); value != "" {
		m.model = value
	}
	eventType := strings.ToLower(event.Type)
	if strings.Contains(eventType, "stream") || strings.Contains(eventType, "token") || strings.Contains(eventType, "delta") || strings.Contains(eventType, "content") {
		m.streamEvents++
		if m.firstOutputAt.IsZero() && (event.Text != "" || nestedText(payload, "text", "content", "delta") != "") {
			m.firstOutputAt = time.Now()
		}
	}
	m.inputTokens = maxInt64(m.inputTokens, nestedInteger(payload, "input_tokens", "prompt_tokens"))
	m.outputTokens = maxInt64(m.outputTokens, nestedInteger(payload, "output_tokens", "completion_tokens"))
	cacheRead := nestedInteger(payload, "cache_read_tokens", "cached_tokens")
	cacheWrite := nestedInteger(payload, "cache_write_tokens")
	m.cacheReadTokens = maxInt64(m.cacheReadTokens, cacheRead)
	m.cacheWriteTokens = maxInt64(m.cacheWriteTokens, cacheWrite)
	m.reasoningTokens = maxInt64(m.reasoningTokens, nestedInteger(payload, "reasoning_tokens"))
	m.cachedTokens = maxInt64(m.cachedTokens, cacheRead+cacheWrite)
	m.costUSD = maxFloat64(m.costUSD, nestedFloat(payload, "cost_usd", "total_cost", "cost"))

	kind, name, phase := eventNode(event)
	if kind == "" || name == "" {
		return
	}
	key := eventCallKey(event, kind, name)
	if phase == "start" {
		if _, exists := m.active[key]; exists {
			return
		}
		_, span := otel.Tracer("open-lumora/services/conversations").Start(ctx, "Hermes."+title(kind))
		m.decorateNodeSpan(span, kind, name)
		m.active[key] = span
		return
	}
	terminalKey := key + ":" + strings.ToLower(event.Type)
	if _, exists := m.seen[terminalKey]; exists {
		return
	}
	m.seen[terminalKey] = struct{}{}
	span, exists := m.active[key]
	if !exists {
		_, span = otel.Tracer("open-lumora/services/conversations").Start(ctx, "Hermes."+title(kind))
		m.decorateNodeSpan(span, kind, name)
	}
	delete(m.active, key)
	span.SetAttributes(attribute.Int64("lumora.response.chars", responseChars(event)))
	if strings.Contains(strings.ToLower(event.Type), "error") || strings.Contains(strings.ToLower(event.Type), "fail") {
		span.SetAttributes(attribute.String("run.status", "error"), attribute.String("error.type", kind+"_failed"))
	} else {
		span.SetAttributes(attribute.String("run.status", "ok"))
	}
	span.End()
}

func (m *runTelemetry) decorateNodeSpan(span trace.Span, kind, name string) {
	span.SetAttributes(m.commonAttributes()...)
	span.SetAttributes(
		attribute.String("lumora.node.kind", kind),
		attribute.String("lumora.node.label", name),
		attribute.String("lumora.node.id_hash", safetracing.HashID(kind+":"+name)),
		attribute.String("lumora.node.parent_id_hash", m.agentID),
		attribute.String(kind+".name", name),
	)
}

func (m *runTelemetry) Close() {
	for key, span := range m.active {
		span.SetAttributes(attribute.String("run.status", "error"), attribute.String("error.type", "incomplete_step"), attribute.Int64("lumora.response.chars", 0))
		span.End()
		delete(m.active, key)
	}
}

func (m *runTelemetry) Apply(span trace.Span) {
	span.SetAttributes(m.commonAttributes()...)
	span.SetAttributes(
		attribute.Int64("gen_ai.usage.input_tokens", m.inputTokens),
		attribute.Int64("gen_ai.usage.output_tokens", m.outputTokens),
		attribute.Int64("gen_ai.usage.cached_tokens", m.cachedTokens),
		attribute.Int64("gen_ai.usage.cache_read_tokens", m.cacheReadTokens),
		attribute.Int64("gen_ai.usage.cache_write_tokens", m.cacheWriteTokens),
		attribute.Int64("gen_ai.usage.reasoning_tokens", m.reasoningTokens),
		attribute.Float64("gen_ai.usage.cost_usd", m.costUSD),
		attribute.Int64("lumora.stream.event_count", m.streamEvents),
		attribute.Float64("lumora.stream.duration_ms", float64(time.Since(m.startedAt).Milliseconds())),
	)
	if !m.firstOutputAt.IsZero() {
		span.SetAttributes(attribute.Float64("lumora.stream.time_to_first_token_ms", float64(m.firstOutputAt.Sub(m.startedAt).Milliseconds())))
	}
}

func (m *runTelemetry) commonAttributes() []attribute.KeyValue {
	return []attribute.KeyValue{
		attribute.String("agent.id_hash", m.agentID),
		attribute.String("agent.name", m.agentName),
		attribute.String("gen_ai.provider.name", m.provider),
		attribute.String("gen_ai.request.model", m.model),
	}
}

func eventNode(event runtimeadapter.Event) (string, string, string) {
	typeName := strings.ToLower(event.Type)
	started := strings.Contains(typeName, "start") || strings.Contains(typeName, "begin") || strings.Contains(typeName, "requested")
	terminal := strings.Contains(typeName, "complete") || strings.Contains(typeName, "result") || strings.Contains(typeName, "finish") || strings.Contains(typeName, "error") || strings.Contains(typeName, "fail")
	if !started && !terminal {
		return "", "", ""
	}
	if strings.Contains(typeName, "tool") {
		return "tool", nestedText(event.Payload, "tool_name", "tool", "name"), eventPhase(started)
	}
	if strings.Contains(typeName, "skill") {
		return "skill", nestedText(event.Payload, "skill_name", "skill", "name"), eventPhase(started)
	}
	return "", "", ""
}

func eventPhase(started bool) string {
	if started {
		return "start"
	}
	return "finish"
}

func eventCallKey(event runtimeadapter.Event, kind, name string) string {
	callID := nestedText(event.Payload, "call_id", "tool_call_id", "invocation_id", "id")
	if callID == "" {
		callID = name
	}
	return kind + ":" + callID
}

// responseChars measures the Unicode character count of a completed tool or
// skill response without retaining or exporting any part of that response.
func responseChars(event runtimeadapter.Event) int64 {
	for _, key := range []string{"output", "result", "response", "content", "text"} {
		if value := nestedValue(event.Payload, key); value != nil {
			return countResponseChars(value)
		}
	}
	return int64(utf8.RuneCountInString(event.Text))
}

func countResponseChars(value any) int64 {
	switch typed := value.(type) {
	case string:
		return int64(utf8.RuneCountInString(typed))
	case []any:
		var total int64
		for _, item := range typed {
			total += countResponseChars(item)
		}
		return total
	case map[string]any:
		var total int64
		for _, item := range typed {
			total += countResponseChars(item)
		}
		return total
	case fmt.Stringer:
		return int64(utf8.RuneCountInString(typed.String()))
	default:
		return 0
	}
}

func title(value string) string {
	if value == "" {
		return value
	}
	return strings.ToUpper(value[:1]) + value[1:]
}

func nestedText(values map[string]any, keys ...string) string {
	value := nestedValue(values, keys...)
	text, _ := value.(string)
	return strings.TrimSpace(text)
}

func nestedInteger(values map[string]any, keys ...string) int64 {
	return int64(nestedFloat(values, keys...))
}

func nestedFloat(values map[string]any, keys ...string) float64 {
	switch value := nestedValue(values, keys...).(type) {
	case float64:
		return value
	case float32:
		return float64(value)
	case int:
		return float64(value)
	case int64:
		return float64(value)
	case jsonNumber:
		parsed, _ := value.Float64()
		return parsed
	}
	return 0
}

type jsonNumber interface {
	Float64() (float64, error)
}

func nestedValue(values map[string]any, keys ...string) any {
	for _, key := range keys {
		if value, exists := values[key]; exists {
			return value
		}
	}
	for _, container := range []string{"usage", "metrics", "data", "response"} {
		if child, ok := values[container].(map[string]any); ok {
			if value := nestedValue(child, keys...); value != nil {
				return value
			}
		}
	}
	return nil
}

func maxInt64(left, right int64) int64 {
	if right > left {
		return right
	}
	return left
}

func maxFloat64(left, right float64) float64 {
	if right > left {
		return right
	}
	return left
}

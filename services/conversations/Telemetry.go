// <Summary>
// Telemetry converts safe Hermes run events into OpenTelemetry usage and
// dependency spans without retaining prompts, responses, or tool arguments.
// </Summary>
package conversations

import (
	"context"
	"strings"

	runtimeadapter "github.com/xno/open-lumora/internal/runtime"
	safetracing "github.com/xno/open-lumora/internal/tracing"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"
)

type runTelemetry struct {
	agentID      string
	agentName    string
	provider     string
	model        string
	inputTokens  int64
	outputTokens int64
	cachedTokens int64
	costUSD      float64
	seen         map[string]struct{}
}

func newRunTelemetry(agentID string) *runTelemetry {
	return &runTelemetry{agentID: safetracing.HashID(agentID), seen: make(map[string]struct{})}
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
	m.inputTokens = maxInt64(m.inputTokens, nestedInteger(payload, "input_tokens", "prompt_tokens"))
	m.outputTokens = maxInt64(m.outputTokens, nestedInteger(payload, "output_tokens", "completion_tokens"))
	cacheRead := nestedInteger(payload, "cache_read_tokens", "cached_tokens")
	cacheWrite := nestedInteger(payload, "cache_write_tokens")
	m.cachedTokens = maxInt64(m.cachedTokens, cacheRead+cacheWrite)
	m.costUSD = maxFloat64(m.costUSD, nestedFloat(payload, "cost_usd", "total_cost", "cost"))

	kind, name := eventNode(event)
	if kind == "" || name == "" {
		return
	}
	key := kind + ":" + name + ":" + strings.ToLower(event.Type)
	if _, exists := m.seen[key]; exists {
		return
	}
	m.seen[key] = struct{}{}
	_, span := otel.Tracer("open-lumora/services/conversations").Start(ctx, "Hermes."+strings.Title(kind))
	span.SetAttributes(m.commonAttributes()...)
	span.SetAttributes(attribute.String("lumora.node.kind", kind), attribute.String("lumora.node.label", name), attribute.String(kind+".name", name))
	if strings.Contains(strings.ToLower(event.Type), "error") || strings.Contains(strings.ToLower(event.Type), "fail") {
		span.SetAttributes(attribute.String("run.status", "error"), attribute.String("error.type", kind+"_failed"))
	} else {
		span.SetAttributes(attribute.String("run.status", "ok"))
	}
	span.End()
}

func (m *runTelemetry) Apply(span trace.Span) {
	span.SetAttributes(m.commonAttributes()...)
	span.SetAttributes(
		attribute.Int64("gen_ai.usage.input_tokens", m.inputTokens),
		attribute.Int64("gen_ai.usage.output_tokens", m.outputTokens),
		attribute.Int64("gen_ai.usage.cached_tokens", m.cachedTokens),
		attribute.Float64("gen_ai.usage.cost_usd", m.costUSD),
	)
}

func (m *runTelemetry) commonAttributes() []attribute.KeyValue {
	return []attribute.KeyValue{
		attribute.String("agent.id_hash", m.agentID),
		attribute.String("agent.name", m.agentName),
		attribute.String("gen_ai.provider.name", m.provider),
		attribute.String("gen_ai.request.model", m.model),
	}
}

func eventNode(event runtimeadapter.Event) (string, string) {
	typeName := strings.ToLower(event.Type)
	terminal := strings.Contains(typeName, "complete") || strings.Contains(typeName, "result") || strings.Contains(typeName, "finish") || strings.Contains(typeName, "error") || strings.Contains(typeName, "fail")
	if !terminal {
		return "", ""
	}
	if strings.Contains(typeName, "tool") {
		return "tool", nestedText(event.Payload, "tool_name", "tool", "name")
	}
	if strings.Contains(typeName, "skill") {
		return "skill", nestedText(event.Payload, "skill_name", "skill", "name")
	}
	return "", ""
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

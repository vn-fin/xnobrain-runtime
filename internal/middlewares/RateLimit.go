// <Summary>
// RateLimit turns a quota decision into standard rate-limit headers and a stable HTTP 429 response.
// Services must enforce the same limit because scheduled and internal calls bypass HTTP middleware.
// </Summary>
package middlewares

import (
	"fmt"
	"strconv"

	"github.com/gofiber/fiber/v3"
	"github.com/rs/zerolog/log"
	"github.com/xno/open-lumora/internal/limits"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"
)

type RateLimitCheck func(c fiber.Ctx) (limits.Decision, error)

func RateLimit(check RateLimitCheck) fiber.Handler {
	return func(c fiber.Ctx) error {
		decision, err := check(c)
		if err != nil {
			return c.Status(fiber.StatusInternalServerError).JSON(fiber.Map{
				"success":     false,
				"message":     err.Error(),
				"status_code": fiber.StatusInternalServerError,
			})
		}
		trace.SpanFromContext(c.Context()).SetAttributes(
			attribute.String("limits.resource", string(decision.Resource)),
			attribute.Bool("limits.allowed", decision.Allowed),
			attribute.Int("limits.limit", decision.Limit),
			attribute.Int("limits.used", decision.Used),
			attribute.Int("limits.remaining", decision.Remaining),
		)
		setRateLimitHeaders(c, decision)
		if decision.Allowed {
			return c.Next()
		}
		log.Warn().
			Str("resource", string(decision.Resource)).
			Int("limit", decision.Limit).
			Int("used", decision.Used).
			Str("route", c.Route().Path).
			Msg("quota.denied")
		message := fmt.Sprintf("%s limit reached", decision.Resource)
		return c.Status(fiber.StatusTooManyRequests).JSON(fiber.Map{
			"success":     false,
			"message":     message,
			"status_code": fiber.StatusTooManyRequests,
			"data":        decision,
		})
	}
}

func setRateLimitHeaders(c fiber.Ctx, decision limits.Decision) {
	remaining := decision.Remaining
	if decision.Allowed && remaining > 0 {
		// Decisions describe usage before the admitted mutation; HTTP headers
		// describe the capacity that remains after this request succeeds.
		remaining--
	}
	c.Set("RateLimit-Policy", fmt.Sprintf("%s;q=%d", decision.Resource, decision.Limit))
	c.Set("RateLimit-Limit", strconv.Itoa(decision.Limit))
	c.Set("RateLimit-Remaining", strconv.Itoa(remaining))
	if decision.ResetAt != nil {
		c.Set("RateLimit-Reset", strconv.FormatInt(decision.ResetAt.Unix(), 10))
	}
}

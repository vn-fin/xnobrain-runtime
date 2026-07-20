// <Summary>
// Schedule validates canonical five-field cron/descriptors against an explicit
// IANA timezone and calculates UTC occurrences with daylight-saving semantics.
// File: Schedule.go
// Functions: canonicalSchedule, nextOccurrence, missedOccurrenceWindow
// </Summary>
package crons

import (
	"fmt"
	"strings"
	"time"

	robfigcron "github.com/robfig/cron/v3"
)

var scheduleParser = robfigcron.NewParser(robfigcron.Minute | robfigcron.Hour | robfigcron.Dom | robfigcron.Month | robfigcron.Dow | robfigcron.Descriptor)

// ValidateSchedule returns the canonical five-field schedule after validating
// the separate IANA timezone. Enterprise managed scheduling reuses this parser.
func ValidateSchedule(schedule string, timezone string) (string, error) {
	canonical, _, err := canonicalSchedule(schedule, timezone)
	return canonical, err
}

// NextOccurrence returns the first UTC occurrence strictly after the provided
// instant using the same IANA/DST behavior as local Community scheduling.
func NextOccurrence(schedule string, timezone string, after time.Time) (time.Time, error) {
	return nextOccurrence(schedule, timezone, after)
}

func canonicalSchedule(schedule string, timezone string) (string, robfigcron.Schedule, error) {
	schedule = strings.Join(strings.Fields(schedule), " ")
	if schedule == "" {
		return "", nil, fmt.Errorf("schedule is required")
	}
	if strings.HasPrefix(schedule, "TZ=") || strings.HasPrefix(schedule, "CRON_TZ=") {
		return "", nil, fmt.Errorf("timezone must use the separate timezone field")
	}
	if timezone == "" {
		timezone = "Etc/UTC"
	}
	if _, err := time.LoadLocation(timezone); err != nil {
		return "", nil, fmt.Errorf("invalid IANA timezone %q", timezone)
	}
	parsed, err := scheduleParser.Parse("CRON_TZ=" + timezone + " " + schedule)
	if err != nil {
		return "", nil, fmt.Errorf("invalid cron schedule: %w", err)
	}
	return schedule, parsed, nil
}

func nextOccurrence(schedule string, timezone string, after time.Time) (time.Time, error) {
	_, parsed, err := canonicalSchedule(schedule, timezone)
	if err != nil {
		return time.Time{}, err
	}
	next := parsed.Next(after.UTC())
	if next.IsZero() {
		return time.Time{}, fmt.Errorf("schedule has no future occurrence")
	}
	return next.UTC(), nil
}

func missedOccurrenceWindow(schedule string, timezone string, first time.Time, through time.Time) (int, time.Time, time.Time, error) {
	_, parsed, err := canonicalSchedule(schedule, timezone)
	if err != nil {
		return 0, time.Time{}, time.Time{}, err
	}
	first = first.UTC()
	through = through.UTC()
	if first.After(through) {
		return 0, time.Time{}, time.Time{}, nil
	}
	if constant, ok := parsed.(robfigcron.ConstantDelaySchedule); ok {
		count := int(through.Sub(first)/constant.Delay) + 1
		return count, first, first.Add(time.Duration(count-1) * constant.Delay), nil
	}
	count := 1
	last := first
	for count < 1_000_000 {
		next := parsed.Next(last)
		if next.IsZero() || next.After(through) {
			return count, first, last.UTC(), nil
		}
		count++
		last = next
	}
	return 0, time.Time{}, time.Time{}, fmt.Errorf("missed occurrence count exceeds safety limit")
}

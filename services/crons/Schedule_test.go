// <Summary>
// Schedule tests prove IANA timezone conversion, DST spring/fall behavior,
// canonical validation, and missed-occurrence aggregation under a fake clock.
// File: Schedule_test.go
// Tests: timezone, DST, validation, missed windows
// </Summary>
package crons

import (
	"testing"
	"time"
)

func TestScheduleUsesNamedTimezoneAndSkipsNonexistentDSTTime(t *testing.T) {
	next, err := nextOccurrence("30 9 * * *", "Asia/Ho_Chi_Minh", time.Date(2026, 7, 20, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if expected := time.Date(2026, 7, 20, 2, 30, 0, 0, time.UTC); !next.Equal(expected) {
		t.Fatalf("Vietnam occurrence = %s, want %s", next, expected)
	}

	// America/New_York jumps from 01:59 to 03:00 on 2026-03-08, so
	// a daily 02:30 schedule resumes on March 9 rather than inventing a time.
	next, err = nextOccurrence("30 2 * * *", "America/New_York", time.Date(2026, 3, 8, 5, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if expected := time.Date(2026, 3, 9, 6, 30, 0, 0, time.UTC); !next.Equal(expected) {
		t.Fatalf("spring DST occurrence = %s, want %s", next, expected)
	}
}

func TestScheduleFallDSTAndMissedWindow(t *testing.T) {
	first, err := nextOccurrence("30 1 * * *", "America/New_York", time.Date(2026, 11, 1, 4, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	second, err := nextOccurrence("30 1 * * *", "America/New_York", first)
	if err != nil {
		t.Fatal(err)
	}
	if !first.Equal(time.Date(2026, 11, 1, 5, 30, 0, 0, time.UTC)) || !second.Equal(time.Date(2026, 11, 1, 6, 30, 0, 0, time.UTC)) {
		t.Fatalf("fall DST occurrences = %s and %s", first, second)
	}
	count, windowFirst, last, err := missedOccurrenceWindow("*/15 * * * *", "Etc/UTC", time.Date(2026, 7, 20, 10, 0, 0, 0, time.UTC), time.Date(2026, 7, 20, 11, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if count != 5 || !windowFirst.Equal(time.Date(2026, 7, 20, 10, 0, 0, 0, time.UTC)) || !last.Equal(time.Date(2026, 7, 20, 11, 0, 0, 0, time.UTC)) {
		t.Fatalf("missed window = %d %s %s", count, windowFirst, last)
	}
}

func TestScheduleValidationRejectsEmbeddedTimezoneAndInvalidLocation(t *testing.T) {
	for _, test := range []struct{ schedule, timezone string }{
		{"CRON_TZ=Etc/UTC 0 1 * * *", "Etc/UTC"},
		{"0 1 * *", "Etc/UTC"},
		{"0 1 * * *", "Mars/Olympus"},
	} {
		if _, _, err := canonicalSchedule(test.schedule, test.timezone); err == nil {
			t.Fatalf("accepted invalid schedule %q timezone %q", test.schedule, test.timezone)
		}
	}
}

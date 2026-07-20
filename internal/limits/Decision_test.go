// <Summary>
// Decision tests cover finite, unlimited, and monthly-reset quota behavior.
// </Summary>
package limits

import (
	"testing"
	"time"
)

func TestCheckFiniteAndUnlimitedLimits(t *testing.T) {
	finite := Check(AgentsCreated, 4, 4, 1, nil)
	if finite.Allowed || finite.Remaining != 0 {
		t.Fatalf("finite decision = %#v", finite)
	}
	unlimited := Check(AgentsCreated, -1, 1000, 1, nil)
	if !unlimited.Allowed || unlimited.Remaining != -1 {
		t.Fatalf("unlimited decision = %#v", unlimited)
	}
}

func TestMonthWindowUsesUTCMonth(t *testing.T) {
	start, reset := MonthWindow(time.Date(2026, 7, 31, 23, 0, 0, 0, time.FixedZone("local", 7*60*60)))
	if start.Format(time.RFC3339) != "2026-07-01T00:00:00Z" || reset.Format(time.RFC3339) != "2026-08-01T00:00:00Z" {
		t.Fatalf("window = %s to %s", start, reset)
	}
}

func TestDayWindowUsesUTCDayAcrossLocalMidnight(t *testing.T) {
	start, reset := DayWindow(time.Date(2026, 7, 21, 1, 30, 0, 0, time.FixedZone("local", 7*60*60)))
	if start.Format(time.RFC3339) != "2026-07-20T00:00:00Z" || reset.Format(time.RFC3339) != "2026-07-21T00:00:00Z" {
		t.Fatalf("window = %s to %s", start, reset)
	}
}

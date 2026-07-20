// <Summary>
// Usage repository tests prove that monthly reservations cannot exceed their configured limit.
// </Summary>
package repositories

import (
	"context"
	"testing"
	"time"

	"github.com/xno/open-lumora/internal/studio/contract"
)

func TestMemoryReserveUsageIsAtomic(t *testing.T) {
	repository := NewMemory()
	period := time.Date(2026, 7, 1, 0, 0, 0, 0, time.UTC)
	for expected := 1; expected <= 2; expected++ {
		counter, allowed, err := repository.ReserveUsage(context.Background(), "local", "cron_runs_monthly", period, 2)
		if err != nil || !allowed || counter.Used != expected {
			t.Fatalf("reservation %d = %#v, %v, %v", expected, counter, allowed, err)
		}
	}
	counter, allowed, err := repository.ReserveUsage(context.Background(), "local", "cron_runs_monthly", period, 2)
	if err != nil || allowed || counter.Used != 2 {
		t.Fatalf("exceeded reservation = %#v, %v, %v", counter, allowed, err)
	}
}

func TestMemoryReserveUsageGroupDoesNotPartiallyIncrement(t *testing.T) {
	repository := NewMemory()
	day := time.Date(2026, 7, 20, 0, 0, 0, 0, time.UTC)
	month := time.Date(2026, 7, 1, 0, 0, 0, 0, time.UTC)
	if _, allowed, err := repository.ReserveUsage(context.Background(), "local", "cron_runs_monthly", month, 1); err != nil || !allowed {
		t.Fatalf("seed monthly usage: %v, %v", allowed, err)
	}
	requests := []contract.UsageReservation{
		{Resource: "cron_runs_daily", PeriodStart: day, Limit: 10},
		{Resource: "cron_runs_monthly", PeriodStart: month, Limit: 1},
	}
	_, allowed, err := repository.ReserveUsageGroup(context.Background(), "local", requests)
	if err != nil || allowed {
		t.Fatalf("group reservation = %v, %v", allowed, err)
	}
	daily, err := repository.GetUsage(context.Background(), "local", "cron_runs_daily", day)
	if err != nil || daily.Used != 0 {
		t.Fatalf("daily was partially incremented = %#v, %v", daily, err)
	}
}

func TestMemoryReserveUsageRejectsZeroLimit(t *testing.T) {
	repository := NewMemory()
	period := time.Date(2026, 7, 1, 0, 0, 0, 0, time.UTC)
	counter, allowed, err := repository.ReserveUsage(context.Background(), "local", "cron_runs_monthly", period, 0)
	if err != nil || allowed || counter.Used != 0 {
		t.Fatalf("zero-limit reservation = %#v, %v, %v", counter, allowed, err)
	}
}

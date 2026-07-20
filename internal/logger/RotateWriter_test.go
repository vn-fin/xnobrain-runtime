// <Summary>
// RotateWriter tests prove date rotation and bounded seven-day local retention.
// File: RotateWriter_test.go
// Test: TestDailyWriterRotatesAndRemovesExpiredLogs
// </Summary>
package logger

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestDailyWriterRotatesAndRemovesExpiredLogs(t *testing.T) {
	root := t.TempDir()
	writer, err := newDailyWriter(root, 7)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 7, 20, 12, 0, 0, 0, time.UTC)
	writer.now = func() time.Time { return now }
	for _, name := range []string{"open-lumora-2026-07-13.jsonl", "open-lumora-2026-07-14.jsonl", "unrelated.txt"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte("old\n"), 0o640); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := writer.Write([]byte("today\n")); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, "open-lumora-2026-07-13.jsonl")); !os.IsNotExist(err) {
		t.Fatalf("expired log was retained: %v", err)
	}
	if _, err := os.Stat(filepath.Join(root, "open-lumora-2026-07-14.jsonl")); err != nil {
		t.Fatalf("seventh day was removed: %v", err)
	}
	now = now.AddDate(0, 0, 1)
	if _, err := writer.Write([]byte("tomorrow\n")); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	for _, date := range []string{"2026-07-20", "2026-07-21"} {
		if _, err := os.Stat(filepath.Join(root, "open-lumora-"+date+".jsonl")); err != nil {
			t.Fatalf("rotated log %s missing: %v", date, err)
		}
	}
}

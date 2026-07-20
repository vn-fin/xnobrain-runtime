// <Summary>
// RotateWriter writes daily JSONL process logs under DATA_DIR/logs and removes
// files outside the configured seven-day local retention window.
// File: RotateWriter.go
// Type: dailyWriter
// </Summary>
package logger

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type dailyWriter struct {
	root          string
	retentionDays int
	now           func() time.Time
	mu            sync.Mutex
	date          string
	file          *os.File
}

func newDailyWriter(root string, retentionDays int) (*dailyWriter, error) {
	if retentionDays < 1 {
		return nil, fmt.Errorf("log retention must be positive")
	}
	if err := os.MkdirAll(root, 0o750); err != nil {
		return nil, err
	}
	return &dailyWriter{root: root, retentionDays: retentionDays, now: func() time.Time { return time.Now().UTC() }}, nil
}

func (w *dailyWriter) Write(payload []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if err := w.rotate(); err != nil {
		return 0, err
	}
	return w.file.Write(payload)
}

func (w *dailyWriter) Close() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.file == nil {
		return nil
	}
	err := w.file.Close()
	w.file = nil
	return err
}

func (w *dailyWriter) rotate() error {
	now := w.now().UTC()
	date := now.Format("2006-01-02")
	if w.file != nil && w.date == date {
		return nil
	}
	if w.file != nil {
		if err := w.file.Close(); err != nil {
			return err
		}
	}
	file, err := os.OpenFile(filepath.Join(w.root, "open-lumora-"+date+".jsonl"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o640)
	if err != nil {
		return err
	}
	w.file = file
	w.date = date
	return w.cleanup(now)
}

func (w *dailyWriter) cleanup(now time.Time) error {
	entries, err := os.ReadDir(w.root)
	if err != nil {
		return err
	}
	cutoff := now.UTC().AddDate(0, 0, -(w.retentionDays - 1)).Format("2006-01-02")
	for _, entry := range entries {
		name := entry.Name()
		if entry.IsDir() || !strings.HasPrefix(name, "open-lumora-") || !strings.HasSuffix(name, ".jsonl") {
			continue
		}
		date := strings.TrimSuffix(strings.TrimPrefix(name, "open-lumora-"), ".jsonl")
		if _, err := time.Parse("2006-01-02", date); err == nil && date < cutoff {
			if err := os.Remove(filepath.Join(w.root, name)); err != nil {
				return err
			}
		}
	}
	return nil
}

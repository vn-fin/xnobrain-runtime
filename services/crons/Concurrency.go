// <Summary>
// Concurrency enforces per-user cron parallelism so one tenant cannot consume another tenant's slots.
// </Summary>
package crons

import (
	"context"
	"fmt"
	"sync"
)

type concurrencyBucket struct {
	limit int
	slots chan struct{}
}

func (c *Concurrency) TryAcquire(key string, limit int) (func(), error) {
	if limit < 0 {
		return func() {}, nil
	}
	if limit < 1 {
		return nil, fmt.Errorf("cron parallel capacity is unavailable")
	}
	c.mu.Lock()
	bucket := c.buckets[key]
	if bucket == nil || bucket.limit != limit {
		bucket = &concurrencyBucket{limit: limit, slots: make(chan struct{}, limit)}
		c.buckets[key] = bucket
	}
	c.mu.Unlock()
	select {
	case bucket.slots <- struct{}{}:
		return func() { <-bucket.slots }, nil
	default:
		return nil, fmt.Errorf("cron parallel capacity is exhausted")
	}
}

type Concurrency struct {
	mu      sync.Mutex
	buckets map[string]*concurrencyBucket
}

func NewConcurrency() *Concurrency {
	return &Concurrency{buckets: make(map[string]*concurrencyBucket)}
}

func (c *Concurrency) Acquire(ctx context.Context, key string, limit int) (func(), error) {
	if limit < 0 {
		return func() {}, nil
	}
	if limit < 1 {
		limit = 1
	}
	c.mu.Lock()
	bucket := c.buckets[key]
	if bucket == nil || bucket.limit != limit {
		bucket = &concurrencyBucket{limit: limit, slots: make(chan struct{}, limit)}
		c.buckets[key] = bucket
	}
	c.mu.Unlock()
	select {
	case bucket.slots <- struct{}{}:
		return func() { <-bucket.slots }, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func (c *Concurrency) Usage(key string) int {
	c.mu.Lock()
	defer c.mu.Unlock()
	if bucket := c.buckets[key]; bucket != nil {
		return len(bucket.slots)
	}
	return 0
}

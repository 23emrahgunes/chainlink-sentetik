// memory_pub.go
// GHOST ORACLE v5.0 :: Redis Stream yayinci (in-memory data bus).
// KISIT: XADD her zaman MAXLEN ~10 ile calisir — RAM'i sabit tutar (zero-disk).
package main

import (
	"context"

	"github.com/redis/go-redis/v9"
)

// Redis Stream anahtarlari.
const (
	StreamCEX       = "stream:cex_l2"
	StreamDEX       = "stream:dex_mempool"
	StreamPoly      = "stream:polymarket"
	StreamChainlink = "stream:chainlink"

	// KISIT: Banana Pi 2GB — her stream'de en fazla ~10 kayit tutulur.
	streamMaxLen = 10
)

// MemoryPub, tek bir Redis client'i sarmalayarak stream yayini yapar.
type MemoryPub struct {
	client *redis.Client
}

// NewMemoryPub yeni bir Redis baglantisi kurar (henuz ping atmaz).
func NewMemoryPub(addr, password string, db int) *MemoryPub {
	rdb := redis.NewClient(&redis.Options{
		Addr:     addr,
		Password: password,
		DB:       db,
		// Dusuk pool — cihaz kaynaklarini korur.
		PoolSize:     8,
		MinIdleConns: 2,
	})
	return &MemoryPub{client: rdb}
}

// Ping baglantiyi dogrular.
func (m *MemoryPub) Ping(ctx context.Context) error {
	return m.client.Ping(ctx).Err()
}

// Publish veriyi ilgili stream'e MAXLEN ~10 kuraliyla basar.
// Approx=true -> Redis'e '~' (yaklasik trim) verir; O(1) performans.
func (m *MemoryPub) Publish(ctx context.Context, stream string, values map[string]interface{}) error {
	return m.client.XAdd(ctx, &redis.XAddArgs{
		Stream: stream,
		MaxLen: streamMaxLen,
		Approx: true, // MAXLEN ~10
		Values: values,
	}).Err()
}

// Close baglantiyi kapatir.
func (m *MemoryPub) Close() error {
	return m.client.Close()
}

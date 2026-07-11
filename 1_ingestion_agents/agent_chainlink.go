// agent_chainlink.go
// GHOST ORACLE v5.0 :: Ajan 1.4 — Polymarket RTDS (gercek Chainlink BTC/USD canli).
// wss://ws-live-data.polymarket.com uzerinden market'in cozuldugu birebir fiyat.
// (Kaynak: kullanicinin polymarket-lab projesi.)
package main

import (
	"context"
	"encoding/json"
	"log"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

const (
	rtdsURL = "wss://ws-live-data.polymarket.com"
	rtdsSub = `{"action":"subscribe","subscriptions":[{"topic":"crypto_prices_chainlink","type":"*","filters":"{\"symbol\":\"btc/usd\"}"}]}`
)

type rtdsMsg struct {
	Payload struct {
		Symbol    string          `json:"symbol"`
		Value     json.RawMessage `json:"value"` // sayi ya da string olabilir
		Timestamp int64           `json:"timestamp"`
	} `json:"payload"`
}

// RunChainlinkRTDS: RTDS baglanti dongusu (ctx iptaline kadar otomatik reconnect).
func RunChainlinkRTDS(ctx context.Context, wg *sync.WaitGroup, pub *MemoryPub) {
	defer wg.Done()
	for {
		select {
		case <-ctx.Done():
			log.Printf("[CHAINLINK] durduruldu")
			return
		default:
		}
		if err := connectRTDS(ctx, pub); err != nil && ctx.Err() == nil {
			log.Printf("[CHAINLINK] baglanti hatasi: %v — 2s sonra", err)
			select {
			case <-ctx.Done():
				return
			case <-time.After(2 * time.Second):
			}
		}
	}
}

func connectRTDS(ctx context.Context, pub *MemoryPub) error {
	dialCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()

	c, _, err := websocket.DefaultDialer.DialContext(dialCtx, rtdsURL, nil)
	if err != nil {
		return err
	}
	defer c.Close()

	connCtx, connCancel := context.WithCancel(ctx)
	defer connCancel()

	if err := c.WriteMessage(websocket.TextMessage, []byte(rtdsSub)); err != nil {
		return err
	}
	log.Printf("[CHAINLINK] RTDS abone: crypto_prices_chainlink btc/usd")

	// Keepalive: PING her 5s (RTDS ister).
	go func() {
		t := time.NewTicker(5 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-connCtx.Done():
				return
			case <-t.C:
				if err := c.WriteMessage(websocket.TextMessage, []byte("PING")); err != nil {
					return
				}
			}
		}
	}()
	go func() {
		<-connCtx.Done()
		_ = c.SetReadDeadline(time.Now().Add(time.Second))
		_ = c.Close()
	}()

	var m rtdsMsg
	for {
		_, msg, err := c.ReadMessage()
		if err != nil {
			return err
		}
		m.Payload.Symbol = ""
		m.Payload.Value = nil
		if err := json.Unmarshal(msg, &m); err != nil {
			continue
		}
		if m.Payload.Symbol != "btc/usd" || len(m.Payload.Value) == 0 {
			continue
		}
		vs := strings.Trim(string(m.Payload.Value), `"`)
		if _, perr := strconv.ParseFloat(vs, 64); perr != nil {
			continue
		}
		pctx, pcancel := context.WithTimeout(ctx, 500*time.Millisecond)
		if perr := pub.Publish(pctx, StreamChainlink, map[string]interface{}{
			"src":   "chainlink",
			"price": vs,
			"cl_ts": m.Payload.Timestamp,
			"ts":    time.Now().UnixMilli(),
		}); perr != nil && ctx.Err() == nil {
			log.Printf("[CHAINLINK] publish hatasi: %v", perr)
		}
		pcancel()
	}
}

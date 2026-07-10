// agent_dex.go
// GHOST ORACLE v5.0 :: Ajan 1.2 — Polygon mempool dinleyici.
// JSON-RPC eth_subscribe("newPendingTransactions") ile gercek pending tx
// hash'lerini dinler ve stream:dex_mempool'a basar (MAXLEN ~10).
//
// KISIT (2GB Banana Pi): mempool saniyede binlerce tx uretir. Firehose'u
// yutmak icin DEX_SAMPLE_MS aralikli ornekleme yapilir (varsayilan 100ms).
package main

import (
	"context"
	"encoding/json"
	"log"
	"strconv"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

const dexSubscribeReq = `{"jsonrpc":"2.0","id":1,"method":"eth_subscribe","params":["newPendingTransactions"]}`

// eth_subscription bildirimi (yalnizca ihtiyac duyulan alanlar).
type ethSubNotification struct {
	Method string `json:"method"`
	Params struct {
		Subscription string          `json:"subscription"`
		Result       json.RawMessage `json:"result"` // "0x<txhash>"
	} `json:"params"`
}

// RunDEXAgent: baglanti dongusu, ctx iptaline kadar otomatik reconnect.
func RunDEXAgent(ctx context.Context, wg *sync.WaitGroup, pub *MemoryPub, wssURL string) {
	defer wg.Done()
	if wssURL == "" {
		log.Printf("[DEX] URL bos — atlaniyor")
		return
	}

	// Ornekleme araligi (firehose koruma).
	sample := 100 * time.Millisecond
	if v, err := strconv.Atoi(getenv("DEX_SAMPLE_MS", "100")); err == nil && v >= 0 {
		sample = time.Duration(v) * time.Millisecond
	}

	for {
		select {
		case <-ctx.Done():
			log.Printf("[DEX] durduruldu")
			return
		default:
		}
		if err := connectDEX(ctx, pub, wssURL, sample); err != nil && ctx.Err() == nil {
			log.Printf("[DEX] baglanti hatasi: %v — 3s sonra yeniden", err)
			select {
			case <-ctx.Done():
				return
			case <-time.After(3 * time.Second):
			}
		}
	}
}

func connectDEX(ctx context.Context, pub *MemoryPub, wssURL string, sample time.Duration) error {
	dialCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()

	c, _, err := websocket.DefaultDialer.DialContext(dialCtx, wssURL, nil)
	if err != nil {
		return err
	}
	defer c.Close()

	connCtx, connCancel := context.WithCancel(ctx)
	defer connCancel()

	if err := c.WriteMessage(websocket.TextMessage, []byte(dexSubscribeReq)); err != nil {
		return err
	}
	log.Printf("[DEX] eth_subscribe(newPendingTransactions) -> %s", wssURL)

	// ctx iptalinde soketi zorla kapat.
	go func() {
		<-connCtx.Done()
		_ = c.SetReadDeadline(time.Now().Add(time.Second))
		_ = c.Close()
	}()

	var n ethSubNotification
	var lastPub time.Time
	var published uint64

	for {
		_, msg, err := c.ReadMessage()
		if err != nil {
			return err
		}

		n.Method = ""
		n.Params.Result = nil
		if err := json.Unmarshal(msg, &n); err != nil {
			continue
		}
		if n.Method != "eth_subscription" {
			continue // subscribe ack veya alakasiz mesaj
		}

		// Ornekleme: aralik dolmadiysa firehose'u yut.
		if sample > 0 && time.Since(lastPub) < sample {
			continue
		}

		var txHash string
		if err := json.Unmarshal(n.Params.Result, &txHash); err != nil || txHash == "" {
			continue
		}
		lastPub = time.Now()

		pctx, pcancel := context.WithTimeout(ctx, 500*time.Millisecond)
		if perr := pub.Publish(pctx, StreamDEX, map[string]interface{}{
			"src":     "polygon_mempool",
			"tx_hash": txHash,
			"ts":      time.Now().UnixMilli(),
		}); perr != nil && ctx.Err() == nil {
			log.Printf("[DEX] publish hatasi: %v", perr)
		}
		pcancel()

		published++
		if published%100 == 0 {
			log.Printf("[DEX] %d pending tx orneklendi (son: %s)", published, txHash)
		}
	}
}

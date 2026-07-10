// agent_polymarket.go
// GHOST ORACLE v5.0 :: Ajan 1.3 — Polymarket CLOB fiyat beslemesi.
// CLOB "market" WebSocket kanalindan bir outcome token'in order book'unu
// dinler; en iyi bid/ask/mid (0..1 olasilik) degerini stream:polymarket'e basar.
//
// DOGRULAMA NOTU: Polymarket WS subscribe formati ve 'book' event semasi
// resmi dokumana gore teyit edilmelidir. DRY_RUN icin plumbing tamdir.
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

type polyLevel struct {
	Price string `json:"price"`
	Size  string `json:"size"`
}

// polyBookEvent: CLOB market kanali 'book' event'i (minimal).
type polyBookEvent struct {
	EventType string      `json:"event_type"`
	AssetID   string      `json:"asset_id"`
	Bids      []polyLevel `json:"bids"`
	Asks      []polyLevel `json:"asks"`
}

// RunPolymarketAgent: token feed baglanti dongusu (ctx iptaline kadar reconnect).
func RunPolymarketAgent(ctx context.Context, wg *sync.WaitGroup, pub *MemoryPub, wssURL, tokenID string) {
	defer wg.Done()
	if wssURL == "" || tokenID == "" {
		log.Printf("[POLY] WSS veya TOKEN_ID bos — feed atlaniyor")
		return
	}
	for {
		select {
		case <-ctx.Done():
			log.Printf("[POLY] durduruldu")
			return
		default:
		}
		if err := connectPoly(ctx, pub, wssURL, tokenID); err != nil && ctx.Err() == nil {
			log.Printf("[POLY] baglanti hatasi: %v — 3s sonra yeniden", err)
			select {
			case <-ctx.Done():
				return
			case <-time.After(3 * time.Second):
			}
		}
	}
}

func connectPoly(ctx context.Context, pub *MemoryPub, wssURL, tokenID string) error {
	dialCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()

	c, _, err := websocket.DefaultDialer.DialContext(dialCtx, wssURL, nil)
	if err != nil {
		return err
	}
	defer c.Close()

	connCtx, connCancel := context.WithCancel(ctx)
	defer connCancel()

	// market kanali abonelik mesaji.
	sub := `{"assets_ids":["` + tokenID + `"],"type":"market"}`
	if err := c.WriteMessage(websocket.TextMessage, []byte(sub)); err != nil {
		return err
	}
	log.Printf("[POLY] market kanali abone: token=%s", tokenID)

	// Keepalive: Polymarket "PING"/"PONG" (10s). Gerekmezse .env ile devre disi.
	go func() {
		t := time.NewTicker(10 * time.Second)
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

	for {
		_, msg, err := c.ReadMessage()
		if err != nil {
			return err
		}
		// Mesaj bir event dizisi ya da tek event olabilir; ikisini de dene.
		events := parsePolyEvents(msg)
		for i := range events {
			ev := &events[i]
			if ev.EventType != "book" || len(ev.Bids) == 0 || len(ev.Asks) == 0 {
				continue
			}
			bidP, bidQ := bestLevel(ev.Bids, true)
			askP, askQ := bestLevel(ev.Asks, false)
			if bidP == "" || askP == "" {
				continue
			}
			mid := midPrice(bidP, askP)

			pctx, pcancel := context.WithTimeout(ctx, 500*time.Millisecond)
			if perr := pub.Publish(pctx, StreamPoly, map[string]interface{}{
				"src":   "polymarket",
				"token": ev.AssetID,
				"bid_p": bidP,
				"bid_q": bidQ,
				"ask_p": askP,
				"ask_q": askQ,
				"mid":   mid,
				"ts":    time.Now().UnixMilli(),
			}); perr != nil && ctx.Err() == nil {
				log.Printf("[POLY] publish hatasi: %v", perr)
			}
			pcancel()
		}
	}
}

// parsePolyEvents: mesaji event dizisi (tercih) veya tek event olarak cozer.
func parsePolyEvents(msg []byte) []polyBookEvent {
	var arr []polyBookEvent
	if err := json.Unmarshal(msg, &arr); err == nil {
		return arr
	}
	var one polyBookEvent
	if err := json.Unmarshal(msg, &one); err == nil {
		return []polyBookEvent{one}
	}
	return nil
}

// bestLevel: bid icin en yuksek fiyat, ask icin en dusuk fiyat (fiyat, miktar).
func bestLevel(levels []polyLevel, wantMax bool) (string, string) {
	bestP, bestQ := "", ""
	var bestF float64
	first := true
	for _, lv := range levels {
		f, err := strconv.ParseFloat(lv.Price, 64)
		if err != nil {
			continue
		}
		if first || (wantMax && f > bestF) || (!wantMax && f < bestF) {
			bestF, bestP, bestQ, first = f, lv.Price, lv.Size, false
		}
	}
	return bestP, bestQ
}

func midPrice(bidP, askP string) string {
	b, e1 := strconv.ParseFloat(bidP, 64)
	a, e2 := strconv.ParseFloat(askP, 64)
	if e1 != nil || e2 != nil {
		return ""
	}
	return strconv.FormatFloat((b+a)/2, 'f', -1, 64)
}

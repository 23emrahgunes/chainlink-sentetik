// agent_polymarket.go
// GHOST ORACLE v5.0 :: Ajan 1.3 — Polymarket "BTC Up/Down 5m" DINAMIK feed.
//
// Bu piyasalar 5 dakikalik ve Chainlink BTC/USD ile cozuluyor. Token id her
// pencerede degisir. Ajan:
//  1. zamandan aktif pencere slug'ini hesaplar (btc-updown-5m-<ts>, ts%300==0)
//  2. gamma API'den "Up" outcome token id'sini cozer
//  3. CLOB market WS'e abone olur, pencere bitene kadar mid (Up olasiligi) basar
//  4. pencere bitince otomatik yeni markete gecer (rollover)
//
// stream:polymarket alanlari: src, token, up_prob(mid), window_ts, ts
// (Statik POLYMARKET_TOKEN_ID verilirse rollover kapanir, o token kullanilir.)
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

const (
	polyWindowSec = 300 // 5 dakika
	gammaAPI      = "https://gamma-api.polymarket.com/markets?slug="
)

var polyHTTP = &http.Client{Timeout: 10 * time.Second}

type polyLevel struct {
	Price string `json:"price"`
	Size  string `json:"size"`
}

type polyBookEvent struct {
	EventType string      `json:"event_type"`
	AssetID   string      `json:"asset_id"`
	Bids      []polyLevel `json:"bids"`
	Asks      []polyLevel `json:"asks"`
}

// gammaMarket: gamma API market objesinin ihtiyac duyulan alanlari.
type gammaMarket struct {
	ClobTokenIds string `json:"clobTokenIds"` // JSON-string dizi: ["Up","Down"]
	Outcomes     string `json:"outcomes"`
	Closed       bool   `json:"closed"`
}

// resolveTokens: btc-updown-5m-<ts> slug'i icin Up/Down outcome token id'lerini coz.
func resolveTokens(slug string) (string, string, error) {
	resp, err := polyHTTP.Get(gammaAPI + slug)
	if err != nil {
		return "", "", err
	}
	defer resp.Body.Close()

	var markets []gammaMarket
	if err := json.NewDecoder(resp.Body).Decode(&markets); err != nil {
		return "", "", err
	}
	if len(markets) == 0 {
		return "", "", fmt.Errorf("market bulunamadi: %s", slug)
	}
	var ids []string
	if err := json.Unmarshal([]byte(markets[0].ClobTokenIds), &ids); err != nil {
		return "", "", err
	}
	if len(ids) < 2 {
		return "", "", fmt.Errorf("up/down token yok: %s", slug)
	}
	return ids[0], ids[1], nil // [0] = "Up", [1] = "Down"
}

// RunPolymarketAgent: dinamik 5dk market rollover dongusu.
func RunPolymarketAgent(ctx context.Context, wg *sync.WaitGroup, pub *MemoryPub, wssURL, staticToken string) {
	defer wg.Done()
	if wssURL == "" {
		log.Printf("[POLY] WSS bos — feed atlaniyor")
		return
	}

	for {
		select {
		case <-ctx.Done():
			log.Printf("[POLY] durduruldu")
			return
		default:
		}

		var token string
		var downToken string
		var winCtx context.Context
		var winCancel context.CancelFunc
		var winTS int64

		if staticToken != "" {
			// Statik mod — rollover yok.
			token = staticToken
			downToken = ""
			winTS = 0
			winCtx, winCancel = context.WithCancel(ctx)
		} else {
			// Dinamik: aktif 5dk pencereyi coz.
			now := time.Now().Unix()
			winTS = (now / polyWindowSec) * polyWindowSec
			slug := fmt.Sprintf("btc-updown-5m-%d", winTS)
			t, dt, err := resolveTokens(slug)
			if err != nil {
				log.Printf("[POLY] token cozulemedi (%s): %v — 5s sonra", slug, err)
				select {
				case <-ctx.Done():
					return
				case <-time.After(5 * time.Second):
				}
				continue
			}
			token = t
			downToken = dt
			// Pencere bitisinde (winTS+300) baglantiyi kapat, sonraki markete gec.
			winEnd := time.Unix(winTS+polyWindowSec, 0)
			winCtx, winCancel = context.WithDeadline(ctx, winEnd)
			log.Printf("[POLY] aktif 5dk market ts=%d, Up token=%s... (bitis %s)",
				winTS, token[:12], winEnd.UTC().Format("15:04:05"))
		}

		// WS'i pencere bitene / ctx iptaline kadar calistir.
		err := connectPoly(winCtx, pub, wssURL, token, downToken, winTS)
		winCancel()
		if err != nil && ctx.Err() == nil {
			log.Printf("[POLY] baglanti hatasi: %v", err)
			select {
			case <-ctx.Done():
				return
			case <-time.After(2 * time.Second):
			}
		}
	}
}

func connectPoly(ctx context.Context, pub *MemoryPub, wssURL, tokenID, downTokenID string, winTS int64) error {
	dialCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()

	c, _, err := websocket.DefaultDialer.DialContext(dialCtx, wssURL, nil)
	if err != nil {
		return err
	}
	defer c.Close()

	connCtx, connCancel := context.WithCancel(ctx)
	defer connCancel()

	sub := `{"assets_ids":["` + tokenID + `"],"type":"market"}`
	if err := c.WriteMessage(websocket.TextMessage, []byte(sub)); err != nil {
		return err
	}

	// Keepalive (Polymarket PING/PONG 10s).
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
			// Pencere deadline'i doldu ise bu normal — hata degil, rollover.
			if ctx.Err() != nil {
				return nil
			}
			return err
		}
		for _, ev := range parsePolyEvents(msg) {
			if ev.EventType != "book" || len(ev.Bids) == 0 || len(ev.Asks) == 0 {
				continue
			}
			bidP, bidQ := bestLevel(ev.Bids, true)
			askP, askQ := bestLevel(ev.Asks, false)
			if bidP == "" || askP == "" {
				continue
			}
			pctx, pcancel := context.WithTimeout(ctx, 500*time.Millisecond)
			if perr := pub.Publish(pctx, StreamPoly, map[string]interface{}{
				"src":        "polymarket",
				"token":      ev.AssetID,
				"up_token":   tokenID,
				"down_token": downTokenID,
				"bid_p":      bidP,
				"bid_q":      bidQ,
				"ask_p":      askP,
				"ask_q":      askQ,
				"mid":        midPrice(bidP, askP), // Up olasiligi (0..1)
				"window_ts":  winTS,
				"ts":         time.Now().UnixMilli(),
			}); perr != nil && ctx.Err() == nil {
				log.Printf("[POLY] publish hatasi: %v", perr)
			}
			pcancel()
		}
	}
}

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

// bestLevel: bid icin en yuksek, ask icin en dusuk fiyat (fiyat, miktar).
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

// agent_whale.go
// GHOST ORACLE v5.0 :: Ajan 1.5 — Binance Futures aggTrade (BALINA/akis).
// wss://fstream.binance.com/ws/btcusdt@aggTrade — her gerceklesen islem.
// Balina DERINLIKTE degil, AGRESIF ISLEMDE gorunur; bu akis onu yakalar.
//
// KISIT (2GB Banana Pi): BTC perp saniyede yuzlerce islem uretir. Firehose'u
// Redis'e dokmek yerine WHALE_FLUSH_MS araliginda TOPLANIP yayinlanir:
//
//	buy_vol/sell_vol (agresif alim/satim hacmi), max_buy/max_sell (en buyuk tek
//	islem = balina), n (islem sayisi). CVD ve balina mantigi Python'da.
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

// aggTradeMsg: Binance @aggTrade payload'unun minimal alanlari.
//
//	m (isBuyerMaker): true  -> agresor SATICI (market sell)
//	                  false -> agresor ALICI  (market buy)
type aggTradeMsg struct {
	Q string `json:"q"` // miktar (BTC)
	M bool   `json:"m"` // isBuyerMaker
}

// whaleAccum: flush arasi biriken akis (tek yazici read-loop, tek okuyucu flush).
type whaleAccum struct {
	sync.Mutex
	buyVol, sellVol float64
	maxBuy, maxSell float64
	n               int
}

// RunWhaleAgent: baglanti dongusu, ctx iptaline kadar otomatik reconnect.
func RunWhaleAgent(ctx context.Context, wg *sync.WaitGroup, pub *MemoryPub, wssURL string) {
	defer wg.Done()
	if wssURL == "" {
		log.Printf("[WHALE] URL bos — atlaniyor")
		return
	}
	flush := 250 * time.Millisecond
	if v, err := strconv.Atoi(getenv("WHALE_FLUSH_MS", "250")); err == nil && v > 0 {
		flush = time.Duration(v) * time.Millisecond
	}
	for {
		select {
		case <-ctx.Done():
			log.Printf("[WHALE] durduruldu")
			return
		default:
		}
		if err := connectWhale(ctx, pub, wssURL, flush); err != nil && ctx.Err() == nil {
			log.Printf("[WHALE] baglanti hatasi: %v — 2s sonra yeniden", err)
			select {
			case <-ctx.Done():
				return
			case <-time.After(2 * time.Second):
			}
		}
	}
}

func connectWhale(ctx context.Context, pub *MemoryPub, wssURL string, flush time.Duration) error {
	dialCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()

	c, _, err := websocket.DefaultDialer.DialContext(dialCtx, wssURL, nil)
	if err != nil {
		return err
	}
	defer c.Close()
	log.Printf("[WHALE] baglandi (aggTrade) -> %s", wssURL)

	connCtx, connCancel := context.WithCancel(ctx)
	defer connCancel()

	acc := &whaleAccum{}

	// Flush goroutine: her aralikta birikimi yayinla + sifirla.
	go func() {
		t := time.NewTicker(flush)
		defer t.Stop()
		for {
			select {
			case <-connCtx.Done():
				return
			case <-t.C:
				acc.Lock()
				bv, sv, mb, ms, n := acc.buyVol, acc.sellVol, acc.maxBuy, acc.maxSell, acc.n
				acc.buyVol, acc.sellVol, acc.maxBuy, acc.maxSell, acc.n = 0, 0, 0, 0, 0
				acc.Unlock()
				if n == 0 {
					continue // islem yoksa yayin yok
				}
				pctx, pcancel := context.WithTimeout(ctx, 500*time.Millisecond)
				if perr := pub.Publish(pctx, StreamWhale, map[string]interface{}{
					"src":      "binance_perp",
					"buy_vol":  strconv.FormatFloat(bv, 'f', -1, 64),
					"sell_vol": strconv.FormatFloat(sv, 'f', -1, 64),
					"max_buy":  strconv.FormatFloat(mb, 'f', -1, 64),
					"max_sell": strconv.FormatFloat(ms, 'f', -1, 64),
					"n":        strconv.Itoa(n),
					"ts":       time.Now().UnixMilli(),
				}); perr != nil && ctx.Err() == nil {
					log.Printf("[WHALE] publish hatasi: %v", perr)
				}
				pcancel()
			}
		}
	}()

	// ctx iptalinde soketi zorla kapat -> ReadMessage bloke kalmaz.
	go func() {
		<-connCtx.Done()
		_ = c.SetReadDeadline(time.Now().Add(time.Second))
		_ = c.Close()
	}()

	var m aggTradeMsg
	for {
		_, msg, err := c.ReadMessage()
		if err != nil {
			return err
		}
		m.Q, m.M = "", false
		if err := json.Unmarshal(msg, &m); err != nil {
			continue
		}
		q, perr := strconv.ParseFloat(m.Q, 64)
		if perr != nil || q <= 0 {
			continue
		}
		acc.Lock()
		if m.M {
			acc.sellVol += q // agresor satici
			if q > acc.maxSell {
				acc.maxSell = q
			}
		} else {
			acc.buyVol += q // agresor alici
			if q > acc.maxBuy {
				acc.maxBuy = q
			}
		}
		acc.n++
		acc.Unlock()
	}
}

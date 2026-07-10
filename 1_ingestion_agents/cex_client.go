// cex_client.go
// GHOST ORACLE v5.0 :: Ajan 1.1 — Generic CEX WSS kosucusu.
// Her borsa bir CEXAdapter (subscribe + parse) saglar; baglanti/reconnect/
// keepalive/publish mantigi burada ORTAK olarak yonetilir.
package main

import (
	"context"
	"log"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

// TopOfBook: tum borsalar icin normalize edilmis en iyi bid/ask (string fiyat/miktar).
type TopOfBook struct {
	Src  string
	BidP string
	BidQ string
	AskP string
	AskQ string
}

// CEXAdapter: bir borsanin WSS baglanti tanimi.
//   Subscribe : baglantidan hemen sonra gonderilecek JSON mesajlar (nil olabilir).
//   Ping      : periyodik keepalive mesaji (bos ise gonderilmez).
//   Parse     : ham mesaji TopOfBook'a cevirir; (nil,false) -> atla (heartbeat/ack).
type CEXAdapter struct {
	Name         string
	URL          string
	Subscribe    []string
	Ping         string
	PingInterval time.Duration
	Parse        func([]byte) (*TopOfBook, bool)
}

// RunCEX: adapter icin baglanti dongusu. ctx iptaline kadar otomatik reconnect.
func RunCEX(ctx context.Context, wg *sync.WaitGroup, pub *MemoryPub, a CEXAdapter) {
	defer wg.Done()
	if a.URL == "" {
		log.Printf("[%s] URL bos — atlaniyor", a.Name)
		return
	}
	for {
		select {
		case <-ctx.Done():
			log.Printf("[%s] durduruldu", a.Name)
			return
		default:
		}
		if err := connectCEX(ctx, pub, a); err != nil && ctx.Err() == nil {
			log.Printf("[%s] baglanti hatasi: %v — 2s sonra yeniden", a.Name, err)
			select {
			case <-ctx.Done():
				return
			case <-time.After(2 * time.Second):
			}
		}
	}
}

func connectCEX(ctx context.Context, pub *MemoryPub, a CEXAdapter) error {
	dialCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()

	c, _, err := websocket.DefaultDialer.DialContext(dialCtx, a.URL, nil)
	if err != nil {
		return err
	}
	defer c.Close()
	log.Printf("[%s] baglandi", a.Name)

	// Baglanti-yerel context: read/keepalive goroutine'lerini birlikte durdurur.
	connCtx, connCancel := context.WithCancel(ctx)
	defer connCancel()

	// Subscribe mesajlari (tek yazici: henuz keepalive baslamadi).
	for _, sub := range a.Subscribe {
		if err := c.WriteMessage(websocket.TextMessage, []byte(sub)); err != nil {
			return err
		}
	}

	// Keepalive: OKX/Bybit gibi app-level ping isteyen borsalar icin.
	if a.Ping != "" && a.PingInterval > 0 {
		go func() {
			t := time.NewTicker(a.PingInterval)
			defer t.Stop()
			for {
				select {
				case <-connCtx.Done():
					return
				case <-t.C:
					if err := c.WriteMessage(websocket.TextMessage, []byte(a.Ping)); err != nil {
						return
					}
				}
			}
		}()
	}

	// ctx iptalinde soketi zorla kapat -> ReadMessage bloke kalmaz.
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
		tob, ok := a.Parse(msg)
		if !ok {
			continue // heartbeat / ack / tek-tarafli delta — atla
		}
		pctx, pcancel := context.WithTimeout(ctx, 500*time.Millisecond)
		if perr := pub.Publish(pctx, StreamCEX, map[string]interface{}{
			"src":   tob.Src,
			"bid_p": tob.BidP,
			"bid_q": tob.BidQ,
			"ask_p": tob.AskP,
			"ask_q": tob.AskQ,
			"ts":    time.Now().UnixMilli(),
		}); perr != nil && ctx.Err() == nil {
			log.Printf("[%s] publish hatasi: %v", a.Name, perr)
		}
		pcancel()
	}
}

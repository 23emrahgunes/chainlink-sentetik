// cex_bybit.go
// GHOST ORACLE v5.0 :: Ajan 1.1 — Bybit v5 (linear) adapter'i.
// orderbook.1.BTCUSDT (depth 1) abone olur; app-level ping 20s'de bir.
package main

import (
	"encoding/json"
	"sync"
	"time"
)

const bybitSymbol = "BTCUSDT"

type bybitMsg struct {
	Topic string `json:"topic"`
	Data  struct {
		B [][]string `json:"b"` // [ [price, size], ... ]
		A [][]string `json:"a"`
	} `json:"data"`
}

var bybitPool = sync.Pool{New: func() interface{} { return new(bybitMsg) }}

func BybitAdapter(url string) CEXAdapter {
	// orderbook.1 = depth 1: [0] her zaman en iyi seviye. Deltalar tek-tarafli
	// gelebilir; son bilinen tarafi hatirla ki her guncellemede yayinlayabilelim.
	// (Parse tek goroutine'den cagrilir — yaris yok.)
	var bp, bq, ap, aq string
	return CEXAdapter{
		Name:         "bybit",
		URL:          url,
		Subscribe:    []string{`{"op":"subscribe","args":["orderbook.1.` + bybitSymbol + `"]}`},
		Ping:         `{"op":"ping"}`,
		PingInterval: 20 * time.Second,
		Parse: func(msg []byte) (*TopOfBook, bool) {
			m := bybitPool.Get().(*bybitMsg)
			defer bybitPool.Put(m)
			m.Topic = ""
			m.Data.B, m.Data.A = nil, nil
			if err := json.Unmarshal(msg, m); err != nil {
				return nil, false
			}
			// Son bilinen bid/ask'i guncelle (qty "0" = seviye kaldirildi, atla).
			if len(m.Data.B) > 0 && len(m.Data.B[0]) >= 2 && m.Data.B[0][1] != "0" {
				bp, bq = m.Data.B[0][0], m.Data.B[0][1]
			}
			if len(m.Data.A) > 0 && len(m.Data.A[0]) >= 2 && m.Data.A[0][1] != "0" {
				ap, aq = m.Data.A[0][0], m.Data.A[0][1]
			}
			if bp == "" || ap == "" {
				return nil, false // henuz iki taraf da gelmedi
			}
			return &TopOfBook{Src: "bybit", BidP: bp, BidQ: bq, AskP: ap, AskQ: aq}, true
		},
	}
}

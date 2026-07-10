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
			// snapshot/delta yalnizca iki taraf da doluysa yayinla.
			if len(m.Data.B) == 0 || len(m.Data.A) == 0 {
				return nil, false
			}
			if len(m.Data.B[0]) < 2 || len(m.Data.A[0]) < 2 {
				return nil, false
			}
			return &TopOfBook{
				Src:  "bybit",
				BidP: m.Data.B[0][0], BidQ: m.Data.B[0][1],
				AskP: m.Data.A[0][0], AskQ: m.Data.A[0][1],
			}, true
		},
	}
}

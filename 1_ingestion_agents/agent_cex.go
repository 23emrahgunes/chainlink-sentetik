// agent_cex.go
// GHOST ORACLE v5.0 :: Ajan 1.1 — Binance Futures adapter'i.
// @depth20 payload'u dogrudan gelir (subscribe gerekmez). sync.Pool ile
// GC baskisi minimize edilir (Banana Pi 2GB RAM).
package main

import (
	"encoding/json"
	"sync"
)

// binanceDepth: @depth20 payload'unun minimal parse struct'i.
type binanceDepth struct {
	Bids [][]string `json:"bids"`
	Asks [][]string `json:"asks"`
}

var binancePool = sync.Pool{New: func() interface{} { return new(binanceDepth) }}

// BinanceAdapter: URL stream path'i icerir (btcusdt@depth20@100ms), subscribe yok.
func BinanceAdapter(url string) CEXAdapter {
	return CEXAdapter{
		Name: "binance",
		URL:  url,
		Parse: func(msg []byte) (*TopOfBook, bool) {
			d := binancePool.Get().(*binanceDepth)
			defer binancePool.Put(d)
			if err := json.Unmarshal(msg, d); err != nil {
				return nil, false
			}
			if len(d.Bids) == 0 || len(d.Asks) == 0 {
				return nil, false
			}
			return &TopOfBook{
				Src:  "binance",
				BidP: d.Bids[0][0], BidQ: d.Bids[0][1],
				AskP: d.Asks[0][0], AskQ: d.Asks[0][1],
				BidVol: sumLevels(d.Bids), AskVol: sumLevels(d.Asks), // 20 seviye derinlik
			}, true
		},
	}
}

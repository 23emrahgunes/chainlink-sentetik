// cex_coinbase.go
// GHOST ORACLE v5.0 :: Ajan 1.1 — Coinbase Exchange adapter'i.
// 'ticker' kanali (auth gerektirmez) en iyi bid/ask verir. App-ping gerekmez.
// NOT: pair yoksa (BTC-USDT) coinbaseProduct'i BTC-USD yapabilirsin.
package main

import (
	"encoding/json"
	"sync"
)

const coinbaseProduct = "BTC-USD" // USDT cifti Coinbase'de ince; USD cok daha aktif

type coinbaseTicker struct {
	Type        string `json:"type"`
	BestBid     string `json:"best_bid"`
	BestBidSize string `json:"best_bid_size"`
	BestAsk     string `json:"best_ask"`
	BestAskSize string `json:"best_ask_size"`
}

var coinbasePool = sync.Pool{New: func() interface{} { return new(coinbaseTicker) }}

func CoinbaseAdapter(url string) CEXAdapter {
	return CEXAdapter{
		Name: "coinbase",
		URL:  url,
		Subscribe: []string{
			`{"type":"subscribe","product_ids":["` + coinbaseProduct + `"],"channels":["ticker"]}`,
		},
		Parse: func(msg []byte) (*TopOfBook, bool) {
			t := coinbasePool.Get().(*coinbaseTicker)
			defer coinbasePool.Put(t)
			*t = coinbaseTicker{}
			if err := json.Unmarshal(msg, t); err != nil {
				return nil, false
			}
			if t.Type != "ticker" || t.BestBid == "" || t.BestAsk == "" {
				return nil, false // subscriptions / heartbeat mesajlari
			}
			return &TopOfBook{
				Src:  "coinbase",
				BidP: t.BestBid, BidQ: t.BestBidSize,
				AskP: t.BestAsk, AskQ: t.BestAskSize,
			}, true
		},
	}
}

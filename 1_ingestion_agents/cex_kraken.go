// cex_kraken.go
// GHOST ORACLE v5.0 :: Ajan 1.1 — Kraken v2 adapter'i (ticker kanali).
// 'book' delta'lari sirasizdir ([0] en iyi olmayabilir); 'ticker' kanali
// dogrudan en iyi bid/ask verir ve sik gunceller. Fiyat/miktar SAYI -> string.
package main

import (
	"encoding/json"
	"strconv"
	"sync"
)

const krakenSymbol = "BTC/USD" // Kraken'de en likit BTC cifti

type krakenTicker struct {
	Bid    float64 `json:"bid"`
	BidQty float64 `json:"bid_qty"`
	Ask    float64 `json:"ask"`
	AskQty float64 `json:"ask_qty"`
}

type krakenMsg struct {
	Channel string         `json:"channel"`
	Data    []krakenTicker `json:"data"`
}

var krakenPool = sync.Pool{New: func() interface{} { return new(krakenMsg) }}

func fmtF(v float64) string { return strconv.FormatFloat(v, 'f', -1, 64) }

func KrakenAdapter(url string) CEXAdapter {
	return CEXAdapter{
		Name: "kraken",
		URL:  url,
		Subscribe: []string{
			`{"method":"subscribe","params":{"channel":"ticker","symbol":["` + krakenSymbol + `"]}}`,
		},
		Parse: func(msg []byte) (*TopOfBook, bool) {
			m := krakenPool.Get().(*krakenMsg)
			defer krakenPool.Put(m)
			m.Channel, m.Data = "", nil
			if err := json.Unmarshal(msg, m); err != nil {
				return nil, false
			}
			if m.Channel != "ticker" || len(m.Data) == 0 {
				return nil, false // heartbeat / status / ack
			}
			d := m.Data[0]
			if d.Bid <= 0 || d.Ask <= 0 {
				return nil, false
			}
			return &TopOfBook{
				Src:  "kraken",
				BidP: fmtF(d.Bid), BidQ: fmtF(d.BidQty),
				AskP: fmtF(d.Ask), AskQ: fmtF(d.AskQty),
			}, true
		},
	}
}

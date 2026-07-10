// cex_kraken.go
// GHOST ORACLE v5.0 :: Ajan 1.1 — Kraken v2 adapter'i.
// book kanali (depth 10) abone olur. Kraken v2 fiyat/miktari SAYI olarak verir,
// stream semasina uymak icin string'e cevrilir. Heartbeat otomatik (app-ping yok).
package main

import (
	"encoding/json"
	"strconv"
	"sync"
)

const krakenSymbol = "BTC/USDT"

type krakenLevel struct {
	Price float64 `json:"price"`
	Qty   float64 `json:"qty"`
}

type krakenMsg struct {
	Channel string `json:"channel"`
	Data    []struct {
		Bids []krakenLevel `json:"bids"`
		Asks []krakenLevel `json:"asks"`
	} `json:"data"`
}

var krakenPool = sync.Pool{New: func() interface{} { return new(krakenMsg) }}

func fmtF(v float64) string { return strconv.FormatFloat(v, 'f', -1, 64) }

func KrakenAdapter(url string) CEXAdapter {
	return CEXAdapter{
		Name: "kraken",
		URL:  url,
		Subscribe: []string{
			`{"method":"subscribe","params":{"channel":"book","symbol":["` + krakenSymbol + `"],"depth":10}}`,
		},
		Parse: func(msg []byte) (*TopOfBook, bool) {
			m := krakenPool.Get().(*krakenMsg)
			defer krakenPool.Put(m)
			m.Channel, m.Data = "", nil
			if err := json.Unmarshal(msg, m); err != nil {
				return nil, false
			}
			if m.Channel != "book" || len(m.Data) == 0 {
				return nil, false // heartbeat / status / ack
			}
			d := m.Data[0]
			if len(d.Bids) == 0 || len(d.Asks) == 0 {
				return nil, false // tek-tarafli update — atla
			}
			return &TopOfBook{
				Src:  "kraken",
				BidP: fmtF(d.Bids[0].Price), BidQ: fmtF(d.Bids[0].Qty),
				AskP: fmtF(d.Asks[0].Price), AskQ: fmtF(d.Asks[0].Qty),
			}, true
		},
	}
}

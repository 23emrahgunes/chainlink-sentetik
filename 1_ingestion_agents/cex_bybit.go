// cex_bybit.go
// GHOST ORACLE v5.0 :: Ajan 1.1 - Bybit v5 linear depth adapter.
package main

import (
	"encoding/json"
	"sync"
	"time"
)

const bybitSymbol = "BTCUSDT"

type bybitMsg struct {
	Type  string `json:"type"`
	Topic string `json:"topic"`
	Data  struct {
		B [][]string `json:"b"`
		A [][]string `json:"a"`
	} `json:"data"`
}

var bybitPool = sync.Pool{New: func() interface{} { return new(bybitMsg) }}

func BybitAdapter(url string) CEXAdapter {
	book := newBookState()
	return CEXAdapter{
		Name:         "bybit",
		URL:          url,
		Subscribe:    []string{`{"op":"subscribe","args":["orderbook.50.` + bybitSymbol + `"]}`},
		Ping:         `{"op":"ping"}`,
		PingInterval: 20 * time.Second,
		Parse: func(msg []byte) (*TopOfBook, bool) {
			m := bybitPool.Get().(*bybitMsg)
			defer bybitPool.Put(m)
			*m = bybitMsg{}
			if err := json.Unmarshal(msg, m); err != nil {
				return nil, false
			}
			if m.Topic == "" || (m.Type != "snapshot" && m.Type != "delta") {
				return nil, false
			}
			if m.Type == "snapshot" {
				book.Reset(m.Data.B, m.Data.A)
			} else {
				book.Apply(m.Data.B, m.Data.A)
			}
			bids, asks := book.Snapshot(50)
			if len(bids) == 0 || len(asks) == 0 {
				return nil, false
			}
			b5, a5, b20, a20, b50, a50 := book.Totals()
			bu10, au10, bu25, au25, bu50, au50, bu100, au100 := book.BandTotals()
			return &TopOfBook{
				Src: "bybit", MarketType: "perp",
				BidP: bids[0][0], BidQ: bids[0][1],
				AskP: asks[0][0], AskQ: asks[0][1],
				BidVol: b50, AskVol: a50,
				BidVol5: b5, AskVol5: a5, BidVol20: b20, AskVol20: a20, BidVol50: b50, AskVol50: a50,
				BidVolUSD10: bu10, AskVolUSD10: au10, BidVolUSD25: bu25, AskVolUSD25: au25,
				BidVolUSD50: bu50, AskVolUSD50: au50, BidVolUSD100: bu100, AskVolUSD100: au100,
			}, true
		},
	}
}

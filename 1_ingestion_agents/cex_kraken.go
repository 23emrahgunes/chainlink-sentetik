// cex_kraken.go
// GHOST ORACLE v5.0 :: Ajan 1.1 - Kraken v2 spot book adapter.
package main

import (
	"encoding/json"
	"sync"
)

const krakenSymbol = "BTC/USD"

type krakenBookMsg struct {
	Channel string `json:"channel"`
	Type    string `json:"type"`
	Data    []struct {
		Bids []numLevel `json:"bids"`
		Asks []numLevel `json:"asks"`
	} `json:"data"`
}

var krakenPool = sync.Pool{New: func() interface{} { return new(krakenBookMsg) }}

func KrakenAdapter(url string) CEXAdapter {
	book := newBookState()
	return CEXAdapter{
		Name: "kraken",
		URL:  url,
		Subscribe: []string{
			`{"method":"subscribe","params":{"channel":"book","symbol":["` + krakenSymbol + `"],"depth":25}}`,
		},
		Parse: func(msg []byte) (*TopOfBook, bool) {
			m := krakenPool.Get().(*krakenBookMsg)
			defer krakenPool.Put(m)
			*m = krakenBookMsg{}
			if err := json.Unmarshal(msg, m); err != nil {
				return nil, false
			}
			if m.Channel != "book" || len(m.Data) == 0 {
				return nil, false
			}
			bids := levelsFromNumeric(m.Data[0].Bids)
			asks := levelsFromNumeric(m.Data[0].Asks)
			if m.Type == "snapshot" {
				book.Reset(bids, asks)
			} else {
				book.Apply(bids, asks)
			}
			topBids, topAsks := book.Snapshot(50)
			if len(topBids) == 0 || len(topAsks) == 0 {
				return nil, false
			}
			b5, a5, b20, a20, b50, a50 := book.Totals()
			bu10, au10, bu25, au25, bu50, au50, bu100, au100 := book.BandTotals()
			return &TopOfBook{
				Src: "kraken", MarketType: "spot",
				BidP: topBids[0][0], BidQ: topBids[0][1],
				AskP: topAsks[0][0], AskQ: topAsks[0][1],
				BidVol: b50, AskVol: a50,
				BidVol5: b5, AskVol5: a5, BidVol20: b20, AskVol20: a20, BidVol50: b50, AskVol50: a50,
				BidVolUSD10: bu10, AskVolUSD10: au10, BidVolUSD25: bu25, AskVolUSD25: au25,
				BidVolUSD50: bu50, AskVolUSD50: au50, BidVolUSD100: bu100, AskVolUSD100: au100,
			}, true
		},
	}
}

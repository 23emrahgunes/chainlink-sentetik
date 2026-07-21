// cex_coinbase.go
// GHOST ORACLE v5.0 :: Ajan 1.1 - Coinbase Exchange level2_batch adapter.
package main

import (
	"encoding/json"
	"sync"
)

const coinbaseProduct = "BTC-USD"

type coinbaseBookMsg struct {
	Type      string     `json:"type"`
	ProductID string     `json:"product_id"`
	Bids      [][]string `json:"bids"`
	Asks      [][]string `json:"asks"`
	Changes   [][]string `json:"changes"` // [side, price, size]
}

var coinbasePool = sync.Pool{New: func() interface{} { return new(coinbaseBookMsg) }}

func CoinbaseAdapter(url string) CEXAdapter {
	book := newBookState()
	return CEXAdapter{
		Name: "coinbase",
		URL:  url,
		Subscribe: []string{
			`{"type":"subscribe","product_ids":["` + coinbaseProduct + `"],"channels":["level2_batch"]}`,
		},
		Parse: func(msg []byte) (*TopOfBook, bool) {
			m := coinbasePool.Get().(*coinbaseBookMsg)
			defer coinbasePool.Put(m)
			*m = coinbaseBookMsg{}
			if err := json.Unmarshal(msg, m); err != nil {
				return nil, false
			}
			switch m.Type {
			case "snapshot":
				book.Reset(m.Bids, m.Asks)
			case "l2update":
				bids, asks := splitCoinbaseChanges(m.Changes)
				book.Apply(bids, asks)
			default:
				return nil, false
			}
			bids, asks := book.Snapshot(50)
			if len(bids) == 0 || len(asks) == 0 {
				return nil, false
			}
			b5, a5, b20, a20, b50, a50 := book.Totals()
			bu10, au10, bu25, au25, bu50, au50, bu100, au100 := book.BandTotals()
			return &TopOfBook{
				Src: "coinbase", MarketType: "spot",
				BidP: bids[0][0], BidQ: bids[0][1],
				AskP: asks[0][0], AskQ: asks[0][1],
				BidVol: b50, AskVol: a50,
				BidVol5: b5, AskVol5: a5, BidVol20: b20, AskVol20: a20, BidVol50: b50, AskVol50: a50,
				BidVolUSD10: bu10, AskVolUSD10: au10, BidVolUSD25: bu25, AskVolUSD25: au25,
				BidVolUSD50: bu50, AskVolUSD50: au50, BidVolUSD100: bu100, AskVolUSD100: au100,
				Bids: bids, Asks: asks,
			}, true
		},
	}
}

func splitCoinbaseChanges(changes [][]string) ([][]string, [][]string) {
	bids, asks := [][]string{}, [][]string{}
	for _, ch := range changes {
		if len(ch) < 3 {
			continue
		}
		lv := []string{ch[1], ch[2]}
		if ch[0] == "buy" {
			bids = append(bids, lv)
		} else if ch[0] == "sell" {
			asks = append(asks, lv)
		}
	}
	return bids, asks
}

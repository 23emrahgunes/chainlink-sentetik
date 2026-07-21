// agent_cex.go
// GHOST ORACLE v5.0 :: Ajan 1.1 - Binance Futures adapter'i.
package main

import (
	"encoding/json"
	"sync"
)

type binanceDepth struct {
	Bids [][]string `json:"bids"`
	Asks [][]string `json:"asks"`
}

var binancePool = sync.Pool{New: func() interface{} { return new(binanceDepth) }}

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
			b5 := sumLevels(d.Bids[:minInt(len(d.Bids), 5)])
			a5 := sumLevels(d.Asks[:minInt(len(d.Asks), 5)])
			b20 := sumLevels(d.Bids)
			a20 := sumLevels(d.Asks)
			bu10, au10, bu25, au25, bu50, au50, bu100, au100 := bandTotalsFromLevels(d.Bids, d.Asks)
			return &TopOfBook{
				Src: "binance", MarketType: "perp",
				BidP: d.Bids[0][0], BidQ: d.Bids[0][1],
				AskP: d.Asks[0][0], AskQ: d.Asks[0][1],
				BidVol: b20, AskVol: a20,
				BidVol5: b5, AskVol5: a5, BidVol20: b20, AskVol20: a20,
				BidVolUSD10: bu10, AskVolUSD10: au10, BidVolUSD25: bu25, AskVolUSD25: au25,
				BidVolUSD50: bu50, AskVolUSD50: au50, BidVolUSD100: bu100, AskVolUSD100: au100,
				Bids: d.Bids, Asks: d.Asks,
			}, true
		},
	}
}

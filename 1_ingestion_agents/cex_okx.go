// cex_okx.go
// GHOST ORACLE v5.0 :: Ajan 1.1 - OKX v5 spot/swap depth adapter.
package main

import (
	"encoding/json"
	"sync"
	"time"
)

const okxInst = "BTC-USDT"
const okxSwapInst = "BTC-USDT-SWAP"

type okxMsg struct {
	Data []struct {
		Asks [][]string `json:"asks"`
		Bids [][]string `json:"bids"`
	} `json:"data"`
}

var okxPool = sync.Pool{New: func() interface{} { return new(okxMsg) }}

func OKXAdapter(url string) CEXAdapter {
	return okxAdapter(url, "okx", okxInst, "spot")
}

func OKXSwapAdapter(url string) CEXAdapter {
	return okxAdapter(url, "okx_swap", okxSwapInst, "perp")
}

func okxAdapter(url, src, inst, marketType string) CEXAdapter {
	return CEXAdapter{
		Name:         src,
		URL:          url,
		Subscribe:    []string{`{"op":"subscribe","args":[{"channel":"books5","instId":"` + inst + `"}]}`},
		Ping:         "ping",
		PingInterval: 25 * time.Second,
		Parse: func(msg []byte) (*TopOfBook, bool) {
			m := okxPool.Get().(*okxMsg)
			defer okxPool.Put(m)
			m.Data = nil
			if err := json.Unmarshal(msg, m); err != nil {
				return nil, false
			}
			if len(m.Data) == 0 {
				return nil, false
			}
			d := m.Data[0]
			if len(d.Bids) == 0 || len(d.Asks) == 0 || len(d.Bids[0]) < 2 || len(d.Asks[0]) < 2 {
				return nil, false
			}
			b5 := sumLevels(d.Bids)
			a5 := sumLevels(d.Asks)
			bu10, au10, bu25, au25, bu50, au50, bu100, au100 := bandTotalsFromLevels(d.Bids, d.Asks)
			return &TopOfBook{
				Src: src, MarketType: marketType,
				BidP: d.Bids[0][0], BidQ: d.Bids[0][1],
				AskP: d.Asks[0][0], AskQ: d.Asks[0][1],
				BidVol: b5, AskVol: a5,
				BidVol5: b5, AskVol5: a5,
				BidVolUSD10: bu10, AskVolUSD10: au10, BidVolUSD25: bu25, AskVolUSD25: au25,
				BidVolUSD50: bu50, AskVolUSD50: au50, BidVolUSD100: bu100, AskVolUSD100: au100,
				Bids: d.Bids, Asks: d.Asks,
			}, true
		},
	}
}

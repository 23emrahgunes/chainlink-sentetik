// cex_okx.go
// GHOST ORACLE v5.0 :: Ajan 1.1 — OKX v5 (public) adapter'i.
// books5 (top-5) abone olur; app-level "ping" (duz metin) 25s'de bir.
package main

import (
	"encoding/json"
	"sync"
	"time"
)

const okxInst = "BTC-USDT"

type okxMsg struct {
	Data []struct {
		Asks [][]string `json:"asks"` // [ [price, size, ...], ... ]
		Bids [][]string `json:"bids"`
	} `json:"data"`
}

var okxPool = sync.Pool{New: func() interface{} { return new(okxMsg) }}

func OKXAdapter(url string) CEXAdapter {
	return CEXAdapter{
		Name:         "okx",
		URL:          url,
		Subscribe:    []string{`{"op":"subscribe","args":[{"channel":"books5","instId":"` + okxInst + `"}]}`},
		Ping:         "ping",
		PingInterval: 25 * time.Second,
		Parse: func(msg []byte) (*TopOfBook, bool) {
			m := okxPool.Get().(*okxMsg)
			defer okxPool.Put(m)
			m.Data = nil
			if err := json.Unmarshal(msg, m); err != nil {
				return nil, false // "pong" / event mesajlari buraya duser
			}
			if len(m.Data) == 0 {
				return nil, false
			}
			d := m.Data[0]
			if len(d.Bids) == 0 || len(d.Asks) == 0 ||
				len(d.Bids[0]) < 2 || len(d.Asks[0]) < 2 {
				return nil, false
			}
			return &TopOfBook{
				Src:  "okx",
				BidP: d.Bids[0][0], BidQ: d.Bids[0][1],
				AskP: d.Asks[0][0], AskQ: d.Asks[0][1],
			}, true
		},
	}
}

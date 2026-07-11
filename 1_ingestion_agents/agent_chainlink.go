// agent_chainlink.go
// GHOST ORACLE v5.0 :: Ajan 1.4 — Chainlink BTC/USD on-chain okuyucu.
// Polymarket'in "Price to Beat" cozum kaynagi Chainlink BTC/USD'dir. Bu ajan
// Polygon uzerindeki Chainlink BTC/USD aggregator'unu ham eth_call ile okuyup
// stream:chainlink'e basar (go-ethereum bagimliligi YOK — sadece net/http).
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"math/big"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	// Polygon mainnet Chainlink BTC/USD aggregator proxy (8 desimal).
	chainlinkBTCUSD = "0xc907E116054Ad103354f2D350FD2514433D57F6f"
	latestRoundData = "0xfeaf968c" // latestRoundData() selector
)

type rpcResp struct {
	Result string `json:"result"`
	Error  *struct {
		Message string `json:"message"`
	} `json:"error"`
}

// RunChainlinkAgent: Chainlink BTC/USD'yi everySec araliginla pollar.
func RunChainlinkAgent(ctx context.Context, wg *sync.WaitGroup, pub *MemoryPub, rpcURL string, everySec int) {
	defer wg.Done()
	if rpcURL == "" {
		log.Printf("[CHAINLINK] RPC bos — atlaniyor")
		return
	}
	if everySec < 1 {
		everySec = 5
	}
	client := &http.Client{Timeout: 8 * time.Second}
	log.Printf("[CHAINLINK] BTC/USD pollama basladi (%ds, Polygon)", everySec)

	read := func() {
		price, updatedAt, err := readChainlink(ctx, client, rpcURL)
		if err != nil {
			if ctx.Err() == nil {
				log.Printf("[CHAINLINK] okuma hatasi: %v", err)
			}
			return
		}
		pctx, pcancel := context.WithTimeout(ctx, 500*time.Millisecond)
		if perr := pub.Publish(pctx, StreamChainlink, map[string]interface{}{
			"src":        "chainlink",
			"price":      strconv.FormatFloat(price, 'f', 2, 64),
			"updated_at": updatedAt,
			"ts":         time.Now().UnixMilli(),
		}); perr != nil && ctx.Err() == nil {
			log.Printf("[CHAINLINK] publish hatasi: %v", perr)
		}
		pcancel()
	}

	read() // ilk degeri hemen al
	ticker := time.NewTicker(time.Duration(everySec) * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			log.Printf("[CHAINLINK] durduruldu")
			return
		case <-ticker.C:
			read()
		}
	}
}

// readChainlink: latestRoundData() cagirir, (price, updatedAt) doner.
func readChainlink(ctx context.Context, client *http.Client, rpcURL string) (float64, int64, error) {
	payload := `{"jsonrpc":"2.0","id":1,"method":"eth_call","params":[{"to":"` +
		chainlinkBTCUSD + `","data":"` + latestRoundData + `"},"latest"]}`
	req, err := http.NewRequestWithContext(ctx, "POST", rpcURL, strings.NewReader(payload))
	if err != nil {
		return 0, 0, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := client.Do(req)
	if err != nil {
		return 0, 0, err
	}
	defer resp.Body.Close()

	var r rpcResp
	if err := json.NewDecoder(resp.Body).Decode(&r); err != nil {
		return 0, 0, err
	}
	if r.Error != nil {
		return 0, 0, fmt.Errorf("rpc: %s", r.Error.Message)
	}
	h := strings.TrimPrefix(r.Result, "0x")
	if len(h) < 320 {
		return 0, 0, fmt.Errorf("kisa sonuc (%d hane)", len(h))
	}
	// latestRoundData -> [roundId, answer, startedAt, updatedAt, answeredInRound]
	ans, ok1 := new(big.Int).SetString(h[64:128], 16)  // answer (8 desimal)
	upd, ok2 := new(big.Int).SetString(h[192:256], 16) // updatedAt (unix)
	if !ok1 || !ok2 {
		return 0, 0, fmt.Errorf("hex parse hatasi")
	}
	priceF, _ := new(big.Float).Quo(new(big.Float).SetInt(ans), big.NewFloat(1e8)).Float64()
	return priceF, upd.Int64(), nil
}

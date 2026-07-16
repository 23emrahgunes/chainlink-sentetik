// main.go
// GHOST ORACLE v5.0 :: Ajan 1 Orkestrator.
// .env okur -> Redis'i test eder -> CEX & DEX goroutine'lerini baslatir
// -> OS sinyaliyle Graceful Shutdown yapar.
package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/joho/godotenv"
)

// getenv: cevre degiskeni yoksa fallback dondurur.
func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func main() {
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)

	// .env: once kok dizin (../.env), sonra yerel â€” sessizce dener.
	_ = godotenv.Load("../.env")
	_ = godotenv.Load(".env")

	mode := getenv("TRADING_MODE", "DRY_RUN")
	redisAddr := getenv("REDIS_ADDR", "127.0.0.1:6379")
	redisPass := getenv("REDIS_PASSWORD", "")
	dexURL := getenv("POLYGON_WSS", "wss://polygon-bor-rpc.publicnode.com")
	polyURL := getenv("POLYMARKET_WSS", "wss://ws-subscriptions-clob.polymarket.com/ws/market")
	polyToken := getenv("POLYMARKET_TOKEN_ID", "")
	whaleURL := getenv("WHALE_WSS", "wss://fstream.binance.com/ws/btcusdt@aggTrade")

	// 5 CEX adapter'i (.env'den URL, bos ise RunCEX atlar).
	cexAdapters := []CEXAdapter{
		BinanceAdapter(getenv("BINANCE_WSS", "wss://fstream.binance.com/ws/btcusdt@depth20@100ms")),
		BybitAdapter(getenv("BYBIT_WSS", "wss://stream.bybit.com/v5/public/linear")),
		OKXAdapter(getenv("OKX_WSS", "wss://ws.okx.com:8443/ws/v5/public")),
		OKXSwapAdapter(getenv("OKX_WSS", "wss://ws.okx.com:8443/ws/v5/public")),
		CoinbaseAdapter(getenv("COINBASE_WSS", "wss://ws-feed.exchange.coinbase.com")),
		KrakenAdapter(getenv("KRAKEN_WSS", "wss://ws.kraken.com/v2")),
	}

	log.Printf("=== GHOST ORACLE v5.0 :: Ingestion Harvester ===")
	log.Printf("TRADING_MODE = %s", mode)

	// --- Redis in-memory bus ---
	pub := NewMemoryPub(redisAddr, redisPass, 0)
	defer pub.Close()

	rootCtx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Baglanti testi.
	pingCtx, pingCancel := context.WithTimeout(rootCtx, 3*time.Second)
	if err := pub.Ping(pingCtx); err != nil {
		pingCancel()
		log.Fatalf("[REDIS] ping BASARISIZ @ %s: %v (docker compose up -d calisiyor mu?)", redisAddr, err)
	}
	pingCancel()
	log.Printf("[REDIS] baglandi @ %s", redisAddr)

	// --- Ajanlari baslat ---
	var wg sync.WaitGroup
	wg.Add(len(cexAdapters) + 4) // CEX adapters + DEX + Polymarket + Chainlink RTDS + Whale
	for _, a := range cexAdapters {
		go RunCEX(rootCtx, &wg, pub, a)
	}
	go RunDEXAgent(rootCtx, &wg, pub, dexURL)
	go RunPolymarketAgent(rootCtx, &wg, pub, polyURL, polyToken)
	go RunChainlinkRTDS(rootCtx, &wg, pub)
	go RunWhaleAgent(rootCtx, &wg, pub, whaleURL)
	log.Printf("[MAIN] %d CEX + DEX + Polymarket + Chainlink RTDS + Whale calisiyor. Ctrl+C ile durdur.", len(cexAdapters))

	// --- Graceful Shutdown ---
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	log.Printf("[MAIN] kapatma sinyali alindi, ajanlar durduruluyor...")
	cancel()

	// Ajanlarin temiz kapanmasini bekle (maks 5s).
	done := make(chan struct{})
	go func() { wg.Wait(); close(done) }()
	select {
	case <-done:
		log.Printf("[MAIN] tum ajanlar temiz kapandi. Cikis.")
	case <-time.After(5 * time.Second):
		log.Printf("[MAIN] kapatma zaman asimi â€” zorla cikis.")
	}
}

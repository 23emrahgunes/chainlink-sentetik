package main

import (
	"encoding/json"
	"sort"
	"strconv"
)

type bookSide map[string]float64

type bookState struct {
	bids bookSide
	asks bookSide
}

func newBookState() *bookState {
	return &bookState{bids: bookSide{}, asks: bookSide{}}
}

func (b *bookState) Reset(bids, asks [][]string) {
	b.bids = bookSide{}
	b.asks = bookSide{}
	b.Apply(bids, asks)
}

func (b *bookState) Apply(bids, asks [][]string) {
	applyLevels(b.bids, bids)
	applyLevels(b.asks, asks)
}

func applyLevels(side bookSide, levels [][]string) {
	for _, lv := range levels {
		if len(lv) < 2 {
			continue
		}
		size, err := strconv.ParseFloat(lv[1], 64)
		if err != nil {
			continue
		}
		if size <= 0 {
			delete(side, lv[0])
			continue
		}
		side[lv[0]] = size
	}
}

func (b *bookState) Snapshot(limit int) ([][]string, [][]string) {
	return topLevels(b.bids, limit, true), topLevels(b.asks, limit, false)
}

func (b *bookState) Totals() (string, string, string, string, string, string) {
	b5, a5 := b.Snapshot(5)
	b20, a20 := b.Snapshot(20)
	b50, a50 := b.Snapshot(50)
	return sumLevels(b5), sumLevels(a5), sumLevels(b20), sumLevels(a20), sumLevels(b50), sumLevels(a50)
}

func (b *bookState) BandTotals() (string, string, string, string, string, string, string, string) {
	bids, asks := b.Snapshot(0)
	return bandTotalsFromLevels(bids, asks)
}

func bandTotalsFromLevels(bids, asks [][]string) (string, string, string, string, string, string, string, string) {
	bands := []float64{10, 25, 50, 100}
	bidTotals := make([]float64, len(bands))
	askTotals := make([]float64, len(bands))
	if len(bids) == 0 || len(asks) == 0 {
		return "", "", "", "", "", "", "", ""
	}
	bestBid, okBid := parseLevelPrice(bids[0])
	bestAsk, okAsk := parseLevelPrice(asks[0])
	if !okBid || !okAsk {
		return "", "", "", "", "", "", "", ""
	}
	for _, lv := range bids {
		price, size, ok := parseLevel(lv)
		if !ok {
			continue
		}
		distance := bestBid - price
		for i, band := range bands {
			if distance >= 0 && distance <= band {
				bidTotals[i] += size
			}
		}
	}
	for _, lv := range asks {
		price, size, ok := parseLevel(lv)
		if !ok {
			continue
		}
		distance := price - bestAsk
		for i, band := range bands {
			if distance >= 0 && distance <= band {
				askTotals[i] += size
			}
		}
	}
	return formatFloat(bidTotals[0]), formatFloat(askTotals[0]),
		formatFloat(bidTotals[1]), formatFloat(askTotals[1]),
		formatFloat(bidTotals[2]), formatFloat(askTotals[2]),
		formatFloat(bidTotals[3]), formatFloat(askTotals[3])
}

func parseLevelPrice(lv []string) (float64, bool) {
	if len(lv) < 1 {
		return 0, false
	}
	price, err := strconv.ParseFloat(lv[0], 64)
	return price, err == nil
}

func parseLevel(lv []string) (float64, float64, bool) {
	if len(lv) < 2 {
		return 0, 0, false
	}
	price, errP := strconv.ParseFloat(lv[0], 64)
	size, errS := strconv.ParseFloat(lv[1], 64)
	return price, size, errP == nil && errS == nil && size > 0
}

func formatFloat(v float64) string {
	return strconv.FormatFloat(v, 'f', -1, 64)
}
func topLevels(side bookSide, limit int, desc bool) [][]string {
	type level struct {
		price float64
		size  float64
		raw   string
	}
	levels := make([]level, 0, len(side))
	for p, s := range side {
		pf, err := strconv.ParseFloat(p, 64)
		if err != nil || s <= 0 {
			continue
		}
		levels = append(levels, level{price: pf, size: s, raw: p})
	}
	sort.Slice(levels, func(i, j int) bool {
		if desc {
			return levels[i].price > levels[j].price
		}
		return levels[i].price < levels[j].price
	})
	if limit > 0 && len(levels) > limit {
		levels = levels[:limit]
	}
	out := make([][]string, 0, len(levels))
	for _, lv := range levels {
		out = append(out, []string{lv.raw, strconv.FormatFloat(lv.size, 'f', -1, 64)})
	}
	return out
}

func levelsFromNumeric(raw []numLevel) [][]string {
	out := make([][]string, 0, len(raw))
	for _, lv := range raw {
		out = append(out, []string{
			strconv.FormatFloat(lv.Price, 'f', -1, 64),
			strconv.FormatFloat(lv.Qty, 'f', -1, 64),
		})
	}
	return out
}

type numLevel struct {
	Price float64 `json:"price"`
	Qty   float64 `json:"qty"`
}

func (l *numLevel) UnmarshalJSON(data []byte) error {
	var raw struct {
		Price any `json:"price"`
		Qty   any `json:"qty"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}
	p, _ := asFloat(raw.Price)
	q, _ := asFloat(raw.Qty)
	l.Price = p
	l.Qty = q
	return nil
}

func asFloat(v any) (float64, bool) {
	switch x := v.(type) {
	case float64:
		return x, true
	case string:
		f, err := strconv.ParseFloat(x, 64)
		return f, err == nil
	default:
		return 0, false
	}
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

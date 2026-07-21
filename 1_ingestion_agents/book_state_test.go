package main

import "testing"

func TestBookStateSnapshotDeltaAndTotals(t *testing.T) {
	b := newBookState()
	b.Reset(
		[][]string{{"100", "1"}, {"99", "2"}, {"98", "3"}},
		[][]string{{"101", "4"}, {"102", "5"}, {"103", "6"}},
	)
	b.Apply(
		[][]string{{"99", "0"}, {"97", "7"}},
		[][]string{{"102", "1.5"}},
	)
	bids, asks := b.Snapshot(2)
	if bids[0][0] != "100" || bids[1][0] != "98" {
		t.Fatalf("unexpected bids: %#v", bids)
	}
	if asks[0][0] != "101" || asks[1][0] != "102" {
		t.Fatalf("unexpected asks: %#v", asks)
	}
	b5, a5, _, _, _, _ := b.Totals()
	if b5 != "11" {
		t.Fatalf("bid total = %s, want 11", b5)
	}
	if a5 != "11.5" {
		t.Fatalf("ask total = %s, want 11.5", a5)
	}
}
func TestBookStateBandTotalsUseUsdDistance(t *testing.T) {
	b := newBookState()
	b.Reset(
		[][]string{{"100", "1"}, {"95", "2"}, {"80", "4"}},
		[][]string{{"101", "3"}, {"120", "5"}, {"160", "7"}},
	)

	bu10, au10, bu25, au25, bu50, au50, bu100, au100 := b.BandTotals()
	if bu10 != "3" || au10 != "3" {
		t.Fatalf("usd10 totals = bid %s ask %s, want bid 3 ask 3", bu10, au10)
	}
	if bu25 != "7" || au25 != "8" {
		t.Fatalf("usd25 totals = bid %s ask %s, want bid 7 ask 8", bu25, au25)
	}
	if bu50 != "7" || au50 != "8" {
		t.Fatalf("usd50 totals = bid %s ask %s, want bid 7 ask 8", bu50, au50)
	}
	if bu100 != "7" || au100 != "15" {
		t.Fatalf("usd100 totals = bid %s ask %s, want bid 7 ask 15", bu100, au100)
	}
}

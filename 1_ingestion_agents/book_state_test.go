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

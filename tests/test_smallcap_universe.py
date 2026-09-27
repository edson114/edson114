"""Unit tests for the cheap prefilter / dedup logic in universe.py."""

from smallcap_options.config import Config
from smallcap_options.data import CandidateQuote
from smallcap_options.universe import cheap_prefilter


def _quote(symbol="AAAA", price=5.0, change_pct=20.0, day_volume=1_000_000, avg_volume_3m=200_000, market_cap=100_000_000):
    return CandidateQuote(
        symbol=symbol,
        name=f"{symbol} Corp",
        price=price,
        change_pct=change_pct,
        day_volume=day_volume,
        avg_volume_3m=avg_volume_3m,
        market_cap=market_cap,
        source_query="test",
    )


def _cfg(**overrides) -> Config:
    from dataclasses import replace

    return replace(Config(), **overrides)


def test_relative_volume_computed_from_fields():
    q = _quote(day_volume=1_000_000, avg_volume_3m=200_000)
    assert q.relative_volume == 5.0


def test_relative_volume_zero_when_avg_missing():
    q = _quote(avg_volume_3m=0.0)
    assert q.relative_volume == 0.0


def test_prefilter_rejects_price_outside_band():
    cfg = _cfg()
    quotes = [_quote(symbol="LOW", price=0.5), _quote(symbol="HIGH", price=50.0), _quote(symbol="OK", price=5.0)]
    kept = {q.symbol for q in cheap_prefilter(quotes, cfg)}
    assert kept == {"OK"}


def test_prefilter_rejects_large_market_cap():
    cfg = _cfg(max_market_cap=50_000_000)
    quotes = [_quote(symbol="BIG", market_cap=1_000_000_000), _quote(symbol="SMALL", market_cap=10_000_000)]
    kept = {q.symbol for q in cheap_prefilter(quotes, cfg)}
    assert kept == {"SMALL"}


def test_prefilter_rejects_low_relative_volume():
    cfg = _cfg(min_relative_volume=3.0)
    quotes = [
        _quote(symbol="THIN", day_volume=100_000, avg_volume_3m=200_000),  # 0.5x
        _quote(symbol="ACTIVE", day_volume=1_000_000, avg_volume_3m=200_000),  # 5x
    ]
    kept = {q.symbol for q in cheap_prefilter(quotes, cfg)}
    assert kept == {"ACTIVE"}


def test_prefilter_rejects_small_moves():
    cfg = _cfg(min_abs_change_pct=8.0)
    quotes = [_quote(symbol="FLAT", change_pct=1.0), _quote(symbol="MOVER", change_pct=-15.0)]
    kept = {q.symbol for q in cheap_prefilter(quotes, cfg)}
    assert kept == {"MOVER"}  # abs() applied, so a big down move also passes


def test_prefilter_dedupes_by_symbol_keeping_first_seen():
    cfg = _cfg()
    quotes = [_quote(symbol="DUPE", change_pct=30.0), _quote(symbol="DUPE", change_pct=31.0)]
    kept = cheap_prefilter(quotes, cfg)
    assert len(kept) == 1


def test_prefilter_ranks_by_relative_volume_times_abs_change():
    cfg = _cfg()
    quotes = [
        _quote(symbol="WEAK", change_pct=10.0, day_volume=300_000, avg_volume_3m=100_000),  # rvol 3, score 30
        _quote(symbol="STRONG", change_pct=40.0, day_volume=1_000_000, avg_volume_3m=100_000),  # rvol 10, score 400
    ]
    kept = cheap_prefilter(quotes, cfg)
    assert [q.symbol for q in kept] == ["STRONG", "WEAK"]

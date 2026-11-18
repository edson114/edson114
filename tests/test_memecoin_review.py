"""Offline tests for --review: parsing saved reports and simulating each pick's plan."""

import datetime as dt
import json

from memecoin_scanner import review, scan
from memecoin_scanner.config import Config, fast_config
from memecoin_scanner.review import Pick, simulate

T0 = dt.datetime(2026, 11, 6, 20, 0, tzinfo=dt.timezone.utc)


def _pick(**kw):
    base = dict(ts=T0, report="r.md", rank=1, symbol="TOK", contract="MINT", score=60, price=1.0,
                action="BUY NOW", entry=1.0, stop=0.75, pool="POOL")
    base.update(kw)
    return Pick(**base)


def _path(prices, start=T0, step_min=15):
    """Candles whose high/low/close walk through `prices` (one candle each)."""
    out, prev = [], prices[0]
    for i, px in enumerate(prices):
        t = start.timestamp() + i * step_min * 60
        out.append((t, prev, max(prev, px), min(prev, px), px))
        prev = px
    return out


def test_stop_before_any_target_loses_the_stop_distance():
    o = simulate(_pick(), _path([1.0, 0.9, 0.7, 2.5]), Config(), 72)
    assert o.event == "STOP" and abs(o.ret - (-0.25)) < 1e-9
    assert o.max_mult == 2.5  # it ran later, but the plan was already out


def test_ladder_sells_half_at_2x_and_holds_the_rest_to_the_end():
    o = simulate(_pick(), _path([1.0, 1.5, 2.1, 3.0]), Config(), 72)
    assert o.event == "TARGET"
    assert abs(o.ret - (0.5 * 2 + 0.5 * 3.0 - 1)) < 1e-9  # half at 2x, half at the last close


def test_full_ladder_with_trailing_stop():
    # 2x (50%), 5x (25%), then trail the last 25% from 10x with a 30% trailing stop.
    o = simulate(_pick(), _path([1.0, 2.0, 5.0, 10.0, 12.0, 8.0, 20.0]), Config(), 72)
    assert o.event == "TARGET"
    assert abs(o.ret - (0.5 * 2 + 0.25 * 5 + 0.25 * 12 * 0.7 - 1)) < 1e-9


def test_stop_after_first_target_keeps_the_banked_half():
    o = simulate(_pick(), _path([1.0, 2.0, 1.2, 0.7]), Config(), 72)
    assert o.event == "TARGET" and abs(o.ret - (0.5 * 2 + 0.5 * 0.75 - 1)) < 1e-9


def test_wait_fills_only_when_the_entry_is_reached_within_24h():
    above = _pick(action="WAIT", price=1.2, entry=1.0, stop=0.8)
    assert simulate(above, _path([1.2, 1.3, 1.25]), Config(), 72).event == "NO FILL"
    filled = simulate(above, _path([1.2, 1.1, 0.99, 2.5]), Config(), 72)
    assert filled.event == "TARGET"
    reclaim = _pick(action="WAIT", price=0.9, entry=1.0, stop=0.8)  # buy only on a climb back
    assert simulate(reclaim, _path([0.9, 0.85, 0.82]), Config(), 72).event == "NO FILL"
    assert simulate(reclaim, _path([0.9, 1.0, 1.4]), Config(), 72).event == "NONE"
    late = _path([1.2] * 100 + [0.9], step_min=15)  # touches the entry after 25h
    assert simulate(above, late, Config(), 72).event == "NO FILL"


def test_old_reports_buy_at_scan_with_the_default_stop():
    o = simulate(_pick(action="OLD", entry=None, stop=None), _path([1.0, 0.7, 0.6]), Config(), 72)
    assert o.event == "STOP" and abs(o.ret - (-0.35)) < 1e-9


def test_fast_mode_ladder_is_used_for_fast_picks():
    o = simulate(_pick(mode="fast"), _path([1.0, 1.5, 1.6]), fast_config(), 72)
    assert o.event == "TARGET" and abs(o.ret - (0.5 * 1.5 + 0.5 * 1.6 - 1)) < 1e-9


def test_candles_outside_the_horizon_are_ignored():
    candles = _path([1.0, 1.1]) + [(T0.timestamp() + 80 * 3600, 1.1, 9.0, 1.1, 9.0)]
    o = simulate(_pick(), candles, Config(), 72)
    assert o.event == "NONE" and o.max_mult < 2


def test_parse_markdown_report(tmp_path):
    md = tmp_path / "2026-11-10-1212.md"
    md.write_text(scan._self_test_report(T0).replace("# Meme Coin Scan: 2026-11-06 20:00 UTC", "# Meme Coin Scan"))
    picks = review.parse_report_md(md)
    assert [p.symbol for p in picks] == ["GOOD", "FRESH"]
    good = picks[0]
    assert good.ts == dt.datetime(2026, 11, 10, 12, 12, tzinfo=dt.timezone.utc)
    assert good.action == "BUY NOW" and good.price == 0.0021 and good.stop and good.entry == 0.0021
    assert good.h1 == 22 and good.age_h == 30 and good.mcap == 1_800_000


def test_sidecar_json_round_trip_and_review_folder_skipped(tmp_path):
    from memecoin_scanner.scan import _synthetic_pair, run_pipeline
    from memecoin_scanner.data import SafetyReport

    pairs = [_synthetic_pair("GOOD", "Good" + "1" * 38 + "pump", T0)]
    picks, _, _ = run_pipeline(
        Config(), discover=lambda: ({"solana": {pairs[0].token_address: set()}}, [], []),
        fetch_pairs=lambda chain, addrs: list(pairs),
        fetch_safety=lambda a: SafetyReport(available=True, mint_authority_revoked=True, freeze_authority_revoked=True,
                                            lp_locked_pct=100.0, total_holders=5000, top10_pct=18.0),
        now=T0,
    )
    (tmp_path / "2026-11-06-2000.md").write_text("# Meme Coin Scan\n")
    (tmp_path / "2026-11-06-2000.json").write_text(review.picks_to_json(picks, Config(), T0, "2026-11-06-2000.md"))
    (tmp_path / "review").mkdir()
    (tmp_path / "review" / "2026-11-07-0000.md").write_text("### 1. $NOPE (x): score 1/100")
    loaded = review.load_picks(tmp_path)
    assert len(loaded) == 1 and loaded[0].symbol == "GOOD" and loaded[0].pool == pairs[0].pair_address
    assert loaded[0].ts == T0 and loaded[0].mode == "standard"


def test_run_review_skips_recent_picks_and_resolves_missing_pools():
    old = _pick(pool="", contract="OLDMINT")
    recent = _pick(ts=T0 + dt.timedelta(days=5), symbol="NEW")
    resolved, fetched = [], []
    outcomes = review.run_review(
        [old, recent], now=T0 + dt.timedelta(days=6), horizon_h=72,
        resolve_pools=lambda cs: resolved.extend(cs) or {"OLDMINT": "OLDPOOL"},
        history=lambda pool, since: fetched.append(pool) or _path([1.0, 2.2]),
    )
    events = {o.pick.symbol: o.event for o in outcomes}
    assert events == {"TOK": "TARGET", "NEW": "TOO RECENT"}
    assert resolved == ["OLDMINT"] and fetched == ["OLDPOOL"]


def test_dead_coins_count_as_stop_outs_not_missing_data():
    dead = _pick(pool="", contract="DEADMINT", entry=None, stop=None)
    [o] = review.run_review([dead], now=T0 + dt.timedelta(days=6), resolve_pools=lambda cs: {},
                            history=lambda pool, since: [])
    assert o.event == "GONE" and abs(o.ret - (-0.35)) < 1e-9
    md = review.render_review([o], 72, T0)
    assert "1 gone" in md and "counted as a stop-out" in md and "| 1 | 0% | 100% | -35%" in md
    [nodata] = review.run_review([_pick()], now=T0 + dt.timedelta(days=6), history=lambda pool, since: [])
    assert nodata.event == "NO DATA" and nodata.ret is None  # a fetch failure isn't a dead coin


def test_render_review_has_summary_and_buckets():
    outs = [
        simulate(_pick(symbol="WIN", score=45, h1=5.0, age_h=30.0), _path([1.0, 2.5]), Config(), 72),
        simulate(_pick(symbol="LOSE", score=80, h1=90.0, age_h=5.0), _path([1.0, 0.6]), Config(), 72),
        review.Outcome(_pick(symbol="WAITER"), "NO FILL"),
    ]
    md = review.render_review(outs, 72, T0)
    assert "3 picks: 1 target, 1 stop, 1 no fill." in md
    assert "| under 50 | 1 | 100% | 0% |" in md and "| 65+ | 1 | 0% | 100% | -25%" in md
    assert "| vertical (>+40%) | 1 | 0% | 100%" in md and "| under 24h | 1 |" in md
    assert "Best: $WIN" in md and "Worst: $LOSE -25%" in md


def test_cli_review(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(review, "load_picks", lambda root: [_pick()])
    monkeypatch.setattr(review, "resolve_pools_live", lambda contracts, timeout: {})
    monkeypatch.setattr(review, "fetch_history", lambda pool, chain, since, horizon, timeout: _path([1.0, 2.0]))
    assert scan.main(["--review", "--output-dir", str(tmp_path)]) == 0
    assert "# Scan Review" in capsys.readouterr().out
    assert len(list((tmp_path / "review").glob("*.md"))) == 1


def test_cli_review_can_grade_one_report(tmp_path, monkeypatch, capsys):
    picks = [_pick(symbol="KEEP", report="2026-11-18-0352.md"), _pick(symbol="SKIPME", report="2026-11-10-1212.md")]
    monkeypatch.setattr(review, "load_picks", lambda root: picks)
    monkeypatch.setattr(review, "fetch_history", lambda pool, chain, since, horizon, timeout: _path([1.0, 2.0]))
    assert scan.main(["--review", "--report", "2026-11-18-0352", "--horizon", "24", "--no-save",
                      "--output-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "$KEEP" in out and "$SKIPME" not in out and "graded on the 24 hours" in out
    assert scan.main(["--review", "--report", "1999-01-01", "--output-dir", str(tmp_path)]) == 1

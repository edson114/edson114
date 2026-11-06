"""Offline unit tests for the meme coin scanner (no network)."""

import datetime as dt
from dataclasses import replace

from memecoin_scanner import data
from memecoin_scanner.config import Config
from memecoin_scanner.data import SafetyReport, best_pair_per_token, parse_pair, parse_rugcheck_report
from memecoin_scanner.report import _price, render_report
from memecoin_scanner.scan import _self_test_report, _synthetic_pair, run_pipeline
from memecoin_scanner.scoring import apply_safety, market_gate, safety_gate, score_market

NOW = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
ADDR = "Tok11111111111111111111111111111111111pump"


def _pair(**kw):
    symbol = kw.pop("symbol", "TOK")
    return _synthetic_pair(symbol, ADDR, NOW, **kw)


def _safe(**kw):
    base = dict(available=True, mint_authority_revoked=True, freeze_authority_revoked=True,
                lp_locked_pct=100.0, total_holders=5_000, top10_pct=18.0)
    base.update(kw)
    return SafetyReport(**base)


# --- parsing -----------------------------------------------------------------

def test_parse_pair_reads_dexscreener_fields():
    raw = {
        "chainId": "solana", "dexId": "pumpswap", "url": "https://dexscreener.com/solana/x",
        "pairAddress": "PAIR", "baseToken": {"address": ADDR, "name": "Tok", "symbol": "TOK"},
        "quoteToken": {"symbol": "SOL"}, "priceUsd": "0.0049",
        "txns": {"h1": {"buys": 10, "sells": 5}}, "volume": {"h24": 1000, "h1": 50},
        "priceChange": {"h1": 12.5}, "liquidity": {"usd": 5000}, "fdv": 90000, "marketCap": 80000,
        "pairCreatedAt": int(NOW.timestamp() * 1000) - 3_600_000,
        "info": {"websites": [{"url": "https://tok.xyz"}], "socials": [{"type": "twitter", "url": "https://x.com/tok"}]},
        "boosts": {"active": 30},
    }
    p = parse_pair(raw)
    assert p.symbol == "TOK" and p.quote_symbol == "SOL"
    assert p.price_usd == 0.0049 and p.market_cap == 80000 and p.liquidity_usd == 5000
    assert p.txns["h1"] == (10, 5) and p.buy_sell_ratio("h1") == 2.0
    assert abs(p.age_hours(NOW) - 1.0) < 1e-6
    assert p.socials == {"twitter": "https://x.com/tok"} and p.websites == ["https://tok.xyz"]
    assert p.boosts_active == 30


def test_parse_pair_falls_back_to_fdv_and_rejects_missing_address():
    assert parse_pair({"baseToken": {}}) is None
    p = parse_pair({"baseToken": {"address": ADDR}, "fdv": 123})
    assert p.market_cap == 123 and p.age_hours(NOW) is None


def test_best_pair_keeps_deepest_liquidity_and_merges_sources():
    a = _pair(liquidity_usd=10_000.0, sources={"boosts"})
    b = _pair(liquidity_usd=90_000.0, pair_address="DEEP", sources={"search"})
    best = best_pair_per_token([a, b])
    assert best[ADDR].pair_address == "DEEP"
    assert best[ADDR].sources == {"boosts", "search"}


def test_rugcheck_parser_excludes_pools_and_burn_from_concentration():
    report = {
        "mintAuthority": None, "freezeAuthority": None, "rugged": False, "totalHolders": 900,
        "graphInsidersDetected": 3, "launchpad": {"name": "Pump.Fun"},
        "knownAccounts": {"AMMOWNER": {"type": "AMM"}},
        "markets": [{"pubkey": "POOL", "liquidityA": "POOLVAULT",
                     "lp": {"lpLockedPct": 100, "lpLockedUSD": 90_000}},
                    {"pubkey": "POOL2", "lp": {"lpLockedPct": 0, "lpLockedUSD": 0}}],
        "topHolders": [
            {"address": "POOLVAULT", "owner": "POOL", "pct": 30.0},
            {"address": "X", "owner": "AMMOWNER", "pct": 10.0},
            {"address": "B", "owner": "11111111111111111111111111111111", "pct": 5.0},
            {"address": "W1", "owner": "whale1", "pct": 4.0},
            {"address": "W2", "owner": "whale2", "pct": 2.5},
        ],
        "risks": [{"name": "Low liquidity", "level": "warn"}, {"name": "Copycat", "level": "danger", "value": "x"}],
    }
    s = parse_rugcheck_report(report)
    assert s.mint_authority_revoked and s.freeze_authority_revoked and not s.rugged
    assert s.top10_pct == 6.5
    assert s.lp_locked_pct == 100.0  # USD-weighted, the empty market adds nothing
    assert s.warn_risks == ["Low liquidity"] and s.danger_risks == ["Copycat (x)"]
    assert s.total_holders == 900 and s.insiders_detected == 3 and s.launchpad == "Pump.Fun"


def test_rugcheck_parser_flags_live_authorities():
    s = parse_rugcheck_report({"mintAuthority": "DEV", "freezeAuthority": "DEV", "topHolders": []})
    assert not s.mint_authority_revoked and not s.freeze_authority_revoked
    assert s.top10_pct == 0


def test_get_json_returns_none_on_errors(monkeypatch):
    class Resp:
        status_code = 404

    monkeypatch.setattr(data._SESSION, "get", lambda *a, **k: Resp())
    assert data._get_json("https://example.invalid", 1, retries=0) is None
    assert data.fetch_safety(ADDR, 1).available is False


# --- gates -------------------------------------------------------------------

def test_market_gate_passes_a_healthy_pair():
    assert market_gate(_pair(), Config(), NOW) is None


def test_market_gate_rejections():
    cfg = Config()
    assert "liquidity" in market_gate(_pair(liquidity_usd=5_000.0), cfg, NOW)
    assert "above ceiling" in market_gate(_pair(market_cap=900_000_000.0), cfg, NOW)
    assert "below floor" in market_gate(_pair(market_cap=50_000.0), cfg, NOW)
    assert "min old" in market_gate(_pair(pair_created_at=NOW - dt.timedelta(minutes=20)), cfg, NOW)
    assert "dumping" in market_gate(_pair(price_change={"h1": -50.0, "h6": 0.0}), cfg, NOW)
    assert "major" in market_gate(_pair(symbol="USDC"), cfg, NOW)
    assert "quoted in" in market_gate(_pair(quote_symbol="FOO"), cfg, NOW)
    assert "volume" in market_gate(_pair(volume={"h24": 10.0}), cfg, NOW)


def test_safety_gate():
    cfg = Config()
    assert safety_gate(_safe(), cfg) is None
    assert safety_gate(SafetyReport(available=False), cfg) is None  # flagged, not rejected
    assert "mint" in safety_gate(_safe(mint_authority_revoked=False), cfg)
    assert "freeze" in safety_gate(_safe(freeze_authority_revoked=False), cfg)
    assert "rugged" in safety_gate(_safe(rugged=True), cfg)
    assert "top-10" in safety_gate(_safe(top10_pct=60.0), cfg)
    assert "danger" in safety_gate(_safe(danger_risks=["Copycat"]), cfg)


# --- scoring -----------------------------------------------------------------

def test_bot_sized_trades_discount_buy_pressure_and_penalize():
    cfg = Config()
    organic = score_market(_pair(), cfg, NOW)  # $80 average trade
    bots = score_market(
        _pair(txns={"h24": (90_000, 60_000), "h6": (40_000, 20_000), "h1": (8_000, 5_000), "m5": (900, 500)}),
        cfg, NOW,
    )  # $400K / 60K trades = ~$7 average
    assert bots.components["buy_pressure"] < 0.5 * organic.components["buy_pressure"]
    assert any("bot micro-trades" in r for _, r in bots.penalties)
    assert not any("bot micro-trades" in r for _, r in organic.penalties)


def test_penalties_for_late_entry_falling_candle_and_missing_socials():
    sc = score_market(
        _pair(price_change={"m5": -15.0, "h1": 10.0, "h6": 30.0, "h24": 4000.0}, socials={}, websites=[]),
        Config(), NOW,
    )
    reasons = " | ".join(r for _, r in sc.penalties)
    assert "late entry" in reasons and "falling right now" in reasons and "no website or socials" in reasons


def test_smaller_cap_scores_more_upside_room():
    cfg = Config()
    small = score_market(_pair(market_cap=400_000.0, liquidity_usd=60_000.0), cfg, NOW)
    big = score_market(_pair(market_cap=20_000_000.0, liquidity_usd=2_000_000.0), cfg, NOW)
    assert small.components["upside_room"] > big.components["upside_room"]


def test_safety_component_and_unverified_flag():
    cfg = Config()
    good = apply_safety(score_market(_pair(), cfg, NOW), _safe(), cfg)
    concentrated = apply_safety(score_market(_pair(), cfg, NOW), _safe(top10_pct=40.0, total_holders=400), cfg)
    unverified = apply_safety(score_market(_pair(), cfg, NOW), SafetyReport(available=False), cfg)
    assert good.components["safety"] > concentrated.components["safety"] > 0
    assert unverified.components["safety"] == 0
    assert any("UNVERIFIED" in f for f in unverified.flags)


def test_score_is_clamped():
    sc = score_market(_pair(), Config(), NOW)
    sc.penalties.append((500, "huge"))
    assert sc.score == 0.0


# --- pipeline + report ---------------------------------------------------------

def _run(pairs, safety, cfg=None):
    cfg = cfg or Config()
    return run_pipeline(
        cfg,
        discover=lambda: ({"solana": {p.token_address: {"test"} for p in pairs}}, [], ["test"]),
        fetch_pairs=lambda chain, addrs: [replace(p, sources=set()) for p in pairs if p.token_address in addrs],
        fetch_safety=lambda addr: safety.get(addr, SafetyReport(available=False)),
        now=NOW,
    )


def test_pipeline_ranks_verified_first_and_caps_top_n():
    pairs = [_synthetic_pair(f"C{i}", f"Addr{i:02d}" + "1" * 34 + "pump", NOW) for i in range(8)]
    safety = {p.token_address: _safe() for p in pairs[1:]}  # C0 unverified
    picks, runners_up, stats = _run(pairs, safety)
    assert len(picks) == 5 and len(runners_up) == 3
    assert all(sc.safety.available for sc in picks)
    assert runners_up[-1].pair.symbol == "C0"  # unverified sinks below verified at equal score
    assert stats.discovered == 8 and stats.passed_market == 8 and stats.passed_safety == 8


def test_pipeline_drops_unsafe_and_counts_rejections():
    good = _synthetic_pair("GOOD", "Good" + "1" * 38 + "pump", NOW)
    rug = _synthetic_pair("RUG", "Rugg" + "1" * 38 + "pump", NOW)
    thin = _synthetic_pair("THIN", "Thin" + "1" * 38 + "pump", NOW, liquidity_usd=1_000.0)
    safety = {good.token_address: _safe(), rug.token_address: _safe(freeze_authority_revoked=False)}
    picks, _, stats = _run([good, rug, thin], safety)
    assert [sc.pair.symbol for sc in picks] == ["GOOD"]
    assert stats.safety_checked == 2 and sum(stats.rejections.values()) == 2


def test_safety_checks_are_capped():
    pairs = [_synthetic_pair(f"C{i}", f"Addr{i:02d}" + "1" * 34 + "pump", NOW) for i in range(6)]
    calls = []
    cfg = replace(Config(), max_safety_checks=3)
    run_pipeline(
        cfg,
        discover=lambda: ({"solana": {p.token_address: set() for p in pairs}}, [], []),
        fetch_pairs=lambda chain, addrs: list(pairs),
        fetch_safety=lambda addr: calls.append(addr) or _safe(),
        now=NOW,
    )
    assert len(calls) == 3


def test_report_renders_contract_plan_and_low_conviction_label():
    cfg = Config()
    sc = apply_safety(score_market(_pair(), cfg, NOW), _safe(), cfg)
    weak = apply_safety(score_market(_pair(), cfg, NOW), _safe(), cfg)
    weak.penalties.append((60, "test penalty"))
    from memecoin_scanner.report import ScanStats

    md = render_report([sc, weak], [], ScanStats(sources_ok=["test"], discovered=2), cfg, NOW)
    assert f"`{ADDR}`" in md and "paste into fomo search" in md
    assert "Stop: $0.001365 (-35%)" in md and "2x ($0.004200)" in md
    assert md.count("(low conviction)") == 1
    assert "Not financial advice" in md


def test_report_with_no_picks_says_sit_out():
    from memecoin_scanner.report import ScanStats

    md = render_report([], [], ScanStats(), Config(), NOW)
    assert "No coin passed every gate" in md


def test_price_formatting_keeps_significant_digits():
    assert _price(0.004856) == "$0.004856"
    assert _price(0.00005369) == "$0.00005369"
    assert _price(1.5) == "$1.5000"
    assert _price(0) == "?"


def test_self_test_report_end_to_end():
    md = _self_test_report(NOW)
    assert "### 1. $GOOD" in md
    for rejected in ("$MINT", "$DUMP", "$THIN", "$WHALE"):
        assert rejected not in md
    assert "whale concentration: 1" in md and "mint not revoked: 1" in md


def test_falling_candle_penalty_grows_with_the_drop():
    cfg = Config()

    def pts(m5):
        sc = score_market(_pair(price_change={"m5": m5, "h1": 10.0, "h6": 30.0, "h24": 50.0}), cfg, NOW)
        return sum(p for p, r in sc.penalties if "falling right now" in r)

    assert pts(-5.0) == 0
    assert pts(-10.0) == 4.0
    assert 7.5 < pts(-22.0) < 8.0
    assert pts(-60.0) == 10.0


# --- entry points ----------------------------------------------------------------

def test_run_scan_console_script_points_at_main():
    import importlib
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text())
    target = pyproject["project"]["scripts"]["run-scan"]
    module_name, func_name = target.split(":")
    from memecoin_scanner.scan import main

    assert getattr(importlib.import_module(module_name), func_name) is main
    assert "memecoin_scanner" in pyproject["tool"]["setuptools"]["packages"]


def test_python_dash_m_package_runs_self_test():
    import subprocess
    import sys
    from pathlib import Path

    out = subprocess.run(
        [sys.executable, "-m", "memecoin_scanner", "--self-test"],
        cwd=Path(__file__).resolve().parent.parent, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert "### 1. $GOOD" in out.stdout

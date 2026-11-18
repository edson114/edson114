"""Offline tests for the paper-trading bot."""

import datetime as dt
import json

from memecoin_scanner import bot, journal, review
from memecoin_scanner.bot import BotConfig
from memecoin_scanner.entry import EntryPlan
from memecoin_scanner.scan import _synthetic_pair
from memecoin_scanner.scoring import Scored

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 18, 4, 0, tzinfo=UTC)
BC = BotConfig()


def _scored(sym, action="BUY NOW", price=1.0, entry=1.0, stop=0.75, score=70.0):
    pair = _synthetic_pair(sym, (sym + "1" * 44)[:44], T0, price_usd=price, pair_address="POOL_" + sym)
    sc = Scored(pair=pair)
    sc.components["test"] = score
    sc.entry = EntryPlan(action=action, price=price, zone_low=entry * 0.95, zone_high=entry * 1.05, entry=entry,
                         stop=stop, swing_low=stop, swing_high=entry * 1.2, method="test", note="")
    return sc


def _path(prices, start=T0, step_min=15):
    out, prev = [], prices[0]
    for i, px in enumerate(prices):
        t = start.timestamp() + i * step_min * 60
        out.append((t, prev, max(prev, px), min(prev, px), px))
        prev = px
    return out


def _run(state, now, picks=(), paths=None, trades=None, bcfg=BC):
    paths = paths or {}
    return bot.run_cycle(state, now, bcfg, scan_picks=lambda: list(picks),
                         history=lambda pool, since: paths.get(pool, []), trades_path=trades)


def test_opens_up_to_the_position_limit_and_skips_skip_and_duplicates(tmp_path):
    state = bot.new_state(BC)
    picks = [_scored(f"C{i}") for i in range(6)]
    picks.insert(1, _scored("SKIPPY", action="SKIP"))
    picks.insert(2, _scored("C0"))  # same coin twice
    log = _run(state, T0, picks, trades=str(tmp_path / "t.csv"))
    syms = [p["symbol"] for p in state["positions"]]
    assert syms == ["C0", "C1", "C2", "C3"] and all(p["status"] == "pending" for p in state["positions"])
    assert state["cash"] == 1000 - 4 * 50 and len(log) == 4 and state["runs"] == 1


def test_buy_now_fills_on_the_next_run_and_is_logged(tmp_path):
    trades = str(tmp_path / "t.csv")
    state = bot.new_state(BC)
    _run(state, T0, [_scored("AAA")], trades=trades)
    log = _run(state, T0 + dt.timedelta(hours=1), paths={"POOL_AAA": _path([1.0, 1.1, 1.2])}, trades=trades)
    pos = state["positions"][0]
    assert pos["status"] == "open" and pos["filled_ts"].startswith("2026-11-18T04:00")
    assert any("bought $50 at 1" in line for line in log)
    rows = journal.load(trades)
    assert len(rows) == 1 and rows[0]["status"] == "OPEN" and rows[0]["symbol"] == "AAA"
    assert pos["value_usd"] < 50 * 1.2  # marked at 1.2 but after costs


def test_stop_closes_the_trade_after_costs(tmp_path):
    trades = str(tmp_path / "t.csv")
    state = bot.new_state(BC)
    _run(state, T0, [_scored("AAA")], trades=trades)
    _run(state, T0 + dt.timedelta(hours=2), paths={"POOL_AAA": _path([1.0, 0.9, 0.7])}, trades=trades)
    pos = state["positions"][0]
    net = 0.75 * 0.98 / 1.02 - 1
    assert pos["status"] == "closed" and pos["event"] == "STOP" and abs(pos["ret_net"] - net) < 1e-9
    assert abs(state["cash"] - (950 + 50 * (1 + net))) < 1e-9
    row = journal.load(trades)[0]
    assert row["status"] == "CLOSED" and abs(float(row["pnl_usd"]) - 50 * net) < 0.01


def test_take_profit_keeps_the_rest_open_and_marked(tmp_path):
    trades = str(tmp_path / "t.csv")
    state = bot.new_state(BC)
    _run(state, T0, [_scored("AAA")], trades=trades)
    _run(state, T0 + dt.timedelta(hours=3), paths={"POOL_AAA": _path([1.0, 2.2, 3.0])}, trades=trades)
    pos = state["positions"][0]
    assert pos["status"] == "open" and pos["event"] == "TARGET"
    gross = 0.5 * 2 + 0.5 * 3.0 - 1
    assert abs(pos["value_usd"] - round(50 * ((1 + gross) * 0.98 / 1.02), 2)) < 0.01


def test_time_limit_sells_whatever_is_left(tmp_path):
    trades = str(tmp_path / "t.csv")
    state = bot.new_state(BC)
    _run(state, T0, [_scored("AAA")], trades=trades)
    flat = _path([1.0] + [1.05] * 300)
    _run(state, T0 + dt.timedelta(hours=73), paths={"POOL_AAA": flat}, trades=trades)
    pos = state["positions"][0]
    assert pos["status"] == "closed" and pos["event"] == "NONE"


def test_wait_order_expires_and_returns_the_cash(tmp_path):
    trades = str(tmp_path / "t.csv")
    state = bot.new_state(BC)
    _run(state, T0, [_scored("AAA", action="WAIT", price=1.0, entry=0.8, stop=0.65)], trades=trades)
    assert state["cash"] == 950
    _run(state, T0 + dt.timedelta(hours=10), paths={"POOL_AAA": _path([1.0, 1.1])}, trades=trades)
    assert state["positions"][0]["status"] == "pending"
    log = _run(state, T0 + dt.timedelta(hours=25), paths={"POOL_AAA": _path([1.0, 1.1, 1.2])}, trades=trades)
    assert state["positions"][0]["status"] == "expired" and state["cash"] == 1000
    assert any("never reached" in line for line in log) and journal.load(trades) == []


def test_wait_order_fills_when_the_entry_is_reached(tmp_path):
    trades = str(tmp_path / "t.csv")
    state = bot.new_state(BC)
    _run(state, T0, [_scored("AAA", action="WAIT", price=1.0, entry=0.8, stop=0.65)], trades=trades)
    _run(state, T0 + dt.timedelta(hours=2), paths={"POOL_AAA": _path([1.0, 0.85, 0.79, 0.9])}, trades=trades)
    pos = state["positions"][0]
    assert pos["status"] == "open" and journal.load(trades)[0]["entry_price"] == "0.8"


def test_cooldown_blocks_reentry_into_a_coin_it_just_left(tmp_path):
    trades = str(tmp_path / "t.csv")
    state = bot.new_state(BC)
    _run(state, T0, [_scored("AAA")], trades=trades)
    stop_path = {"POOL_AAA": _path([1.0, 0.7])}
    _run(state, T0 + dt.timedelta(hours=1), [_scored("AAA")], paths=stop_path, trades=trades)
    assert [p["status"] for p in state["positions"]] == ["closed"]  # closed, and not re-bought
    _run(state, T0 + dt.timedelta(hours=26), [_scored("AAA")], paths=stop_path, trades=trades)
    assert [p["status"] for p in state["positions"]] == ["closed", "pending"]


def test_daily_loss_limit_stops_new_trades(tmp_path):
    state = bot.new_state(BC)
    state["positions"].append(dict(status="closed", closed_ts=T0.isoformat(), value_usd=10.0, stake_usd=50.0,
                                   contract="X", scan_ts=T0.isoformat()))
    log = bot.open_positions(state, [_scored("AAA")], T0 + dt.timedelta(hours=1), BC)
    assert "daily loss limit hit" in log[0] and len(state["positions"]) == 1
    tomorrow = bot.open_positions(state, [_scored("AAA")], T0 + dt.timedelta(days=1), BC)
    assert len(state["positions"]) == 2 and "AAA" in tomorrow[0]


def test_dead_coin_is_closed_as_a_stop_out_after_the_hold_limit(tmp_path):
    trades = str(tmp_path / "t.csv")
    state = bot.new_state(BC)
    _run(state, T0, [_scored("AAA")], trades=trades)
    _run(state, T0 + dt.timedelta(hours=1), paths={"POOL_AAA": _path([1.0, 1.0])}, trades=trades)
    _run(state, T0 + dt.timedelta(hours=10), paths={}, trades=trades)  # data gap: stays open
    assert state["positions"][0]["status"] == "open"
    log = _run(state, T0 + dt.timedelta(hours=73), paths={}, trades=trades)
    pos = state["positions"][0]
    assert pos["status"] == "closed" and pos["event"] == "GONE" and any("no price data" in line for line in log)


def test_failed_scan_still_manages_exits(tmp_path):
    trades = str(tmp_path / "t.csv")
    state = bot.new_state(BC)
    _run(state, T0, [_scored("AAA")], trades=trades)

    def boom():
        raise RuntimeError("DexScreener down")

    log = bot.run_cycle(state, T0 + dt.timedelta(hours=1), BC, scan_picks=boom,
                        history=lambda pool, since: _path([1.0, 0.7]), trades_path=trades)
    assert state["positions"][0]["status"] == "closed" and any("scan failed: DexScreener down" in l for l in log)


def test_status_page(tmp_path):
    trades = str(tmp_path / "t.csv")
    state = bot.new_state(BC)
    _run(state, T0, [_scored("AAA"), _scored("BBB", action="WAIT", entry=0.8, stop=0.65), _scored("CCC")],
         trades=trades)
    paths = {"POOL_AAA": _path([1.0, 1.3]), "POOL_BBB": _path([1.0, 1.1]), "POOL_CCC": _path([1.0, 0.7])}
    _run(state, T0 + dt.timedelta(hours=1), paths=paths, trades=trades)
    md = bot.render_status(state, T0 + dt.timedelta(hours=1), BC, trades)
    assert md.startswith("# Paper Bot") and "Paper trading only" in md
    assert "## Open" in md and "$AAA" in md and "## Waiting for entry" in md and "$BBB" in md
    assert "## Last closed" in md and "$CCC | STOP" in md and "Phase 1 progress: 1/50" in md


def test_cli_once_then_status(tmp_path, monkeypatch, capsys):
    paths = dict(state=tmp_path / "s.json", trades=tmp_path / "t.csv", status=tmp_path / "status.md")
    monkeypatch.setattr(bot, "_live_scan", lambda cfg, now: [_scored("AAA")])
    monkeypatch.setattr(review, "fetch_history", lambda *a, **k: [])
    common = ["--state", str(paths["state"]), "--trades", str(paths["trades"]), "--status-path", str(paths["status"])]
    assert bot.main(["--once"] + common) == 0
    state = json.loads(paths["state"].read_text())
    assert state["runs"] == 1 and state["positions"][0]["symbol"] == "AAA"
    assert paths["status"].read_text().startswith("# Paper Bot")
    assert bot.main(["--status"] + common) == 0
    assert json.loads(paths["state"].read_text())["runs"] == 1  # --status doesn't trade
    assert "$AAA" in capsys.readouterr().out
    assert bot.main(["--once", "--reset", "--cash", "500", "--stake", "25"] + common) == 0
    state = json.loads(paths["state"].read_text())
    assert state["starting_cash"] == 500 and state["positions"][0]["stake_usd"] == 25

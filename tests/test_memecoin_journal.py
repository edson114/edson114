"""Offline tests for the meme coin trade log."""

import pytest

from memecoin_scanner import journal


def _path(tmp_path):
    return str(tmp_path / "trades.csv")


def test_buy_then_sell_all_computes_pnl(tmp_path):
    p = _path(tmp_path)
    tid = journal.buy("$wealth", 200, 0.000347, contract="MINT", opened_at="2026-11-06", path=p)
    assert tid == 1
    row = journal.sell(1, 0.0009313, closed_at="2026-11-07", estimated=True, path=p)
    assert row["status"] == "CLOSED" and row["symbol"] == "WEALTH"
    assert row["pnl_usd"] == "336.77" and row["pnl_pct"] == "168.4"
    assert row["exit_estimated"] == "yes" and row["closed_at"] == "2026-11-07"


def test_partial_sells_follow_the_ladder_and_average_the_exit(tmp_path):
    p = _path(tmp_path)
    journal.buy("TOK", 100, 1.0, path=p)
    assert journal.sell(1, 2.0, fraction=0.5, path=p)["status"] == "PARTIAL"
    assert journal.sell(1, 5.0, fraction=0.25, path=p)["status"] == "PARTIAL"
    row = journal.sell(1, 1.0, path=p)  # the rest
    assert row["status"] == "CLOSED"
    assert row["proceeds_usd"] == "250.00" and row["pnl_usd"] == "150.00"
    assert float(row["exit_price"]) == 2.5


def test_exact_proceeds_override_price(tmp_path):
    p = _path(tmp_path)
    journal.buy("TOK", 100, 1.0, path=p)
    row = journal.sell(1, 2.0, proceeds_usd=190.0, path=p)  # fees + slippage
    assert row["pnl_usd"] == "90.00"


def test_sell_errors(tmp_path):
    p = _path(tmp_path)
    journal.buy("TOK", 100, 1.0, path=p)
    with pytest.raises(ValueError):
        journal.sell(2, 1.0, path=p)  # unknown id
    with pytest.raises(ValueError):
        journal.sell(1, 1.0, fraction=1.5, path=p)  # more than held
    journal.sell(1, 1.0, path=p)
    with pytest.raises(ValueError):
        journal.sell(1, 1.0, path=p)  # already closed
    with pytest.raises(ValueError):
        journal.buy("TOK", 0, 1.0, path=p)


def test_stats_expectancy_and_daily_limit(tmp_path):
    p = _path(tmp_path)
    for price in (2.0, 0.7, 0.6):  # +100%, -30%, -40%
        tid = journal.buy("TOK", 100, 1.0, path=p)
        journal.sell(tid, price, closed_at="2026-11-07", path=p)
    journal.buy("OPEN", 50, 1.0, path=p)
    s = journal.compute_stats(journal.load(p), today="2026-11-07")
    assert (s.closed, s.open, s.wins, s.losses) == (3, 1, 1, 2)
    assert round(s.win_rate) == 33 and s.avg_win_pct == 100.0 and s.avg_loss_pct == -35.0
    assert round(s.expectancy_pct, 1) == 10.0 and s.total_pnl == 30.0 and s.pnl_today == 30.0
    text = journal.render_stats(s, account=1000)
    assert "3/50" in text and "Expectancy: +10.0%" in text and "-$30): ok" in text


def test_daily_limit_hit(tmp_path):
    p = _path(tmp_path)
    journal.buy("TOK", 100, 1.0, path=p)
    journal.sell(1, 0.6, closed_at="2026-11-07", path=p)
    s = journal.compute_stats(journal.load(p), today="2026-11-07")
    assert "HIT: stop trading today" in journal.render_stats(s, account=1000)


def test_cli_round_trip(tmp_path, capsys):
    p = _path(tmp_path)
    assert journal.main(["--path", p, "buy", "--symbol", "WEALTH", "--usd", "200", "--price", "0.000347"]) == 0
    assert journal.main(["--path", p, "sell", "--id", "1", "--price", "0.0009313", "--estimated"]) == 0
    assert journal.main(["--path", p, "list"]) == 0
    assert journal.main(["--path", p, "sell", "--id", "9", "--price", "1"]) == 1
    out = capsys.readouterr().out
    assert "Logged trade #1" in out and "CLOSED, P&L $+336.77" in out and "(est.)" in out


def test_correct_replaces_an_estimated_exit(tmp_path):
    p = _path(tmp_path)
    journal.buy("WEALTH", 200, 0.000347, path=p)
    journal.sell(1, 0.0009313, estimated=True, path=p)
    row = journal.correct(1, proceeds_usd=520.0, path=p)
    assert row["pnl_usd"] == "320.00" and row["pnl_pct"] == "160.0" and row["exit_estimated"] == "no"
    row = journal.correct(1, exit_price=0.0009, path=p)
    assert row["proceeds_usd"] == "518.73"
    with pytest.raises(ValueError):
        journal.correct(1, path=p)
    journal.buy("OPEN", 50, 1.0, path=p)
    with pytest.raises(ValueError):
        journal.correct(2, proceeds_usd=10.0, path=p)  # not closed


def test_stats_show_na_without_losses(tmp_path):
    p = _path(tmp_path)
    journal.buy("TOK", 100, 1.0, path=p)
    journal.sell(1, 2.0, path=p)
    assert "Average loss: n/a" in journal.render_stats(journal.compute_stats(journal.load(p)))

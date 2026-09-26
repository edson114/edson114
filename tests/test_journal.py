"""Tests for the trade journal: CSV persistence, close-out settlement
math, and realized-performance summaries. Everything runs against a
tmp_path CSV so nothing touches the repo's real journal/trades.csv.
"""

import datetime as dt

import pytest

from qqq_iron_condor import journal


def _sample_trade(**overrides):
    defaults = dict(
        label="Weekly", symbol="QQQ", expiration="2026-10-09", dte_at_entry=8,
        short_call=760.0, long_call=765.0, short_put=720.0, long_put=715.0,
        credit=1.35, contracts=1, entry_underlying=740.5, vix_at_entry=15.2,
        opened_at=dt.date(2026, 9, 26),
    )
    defaults.update(overrides)
    return journal.NewTradeInput(**defaults)


def test_add_trade_assigns_sequential_ids_and_computes_derived_fields(tmp_path):
    path = str(tmp_path / "trades.csv")
    id1 = journal.add_trade(_sample_trade(), path=path)
    id2 = journal.add_trade(_sample_trade(), path=path)
    assert id1 == 1
    assert id2 == 2

    df = journal.load_journal(path)
    row = df[df["id"] == 1].iloc[0]
    assert row["status"] == "OPEN"
    assert row["call_width"] == pytest.approx(5.0)
    assert row["put_width"] == pytest.approx(5.0)
    assert row["max_profit"] == pytest.approx(1.35)
    assert row["max_loss"] == pytest.approx(5.0 - 1.35)


def test_add_trade_rejects_inverted_wings(tmp_path):
    path = str(tmp_path / "trades.csv")
    with pytest.raises(ValueError):
        journal.add_trade(_sample_trade(long_call=758.0), path=path)  # long inside short


def test_close_trade_with_exit_debit_computes_pnl(tmp_path):
    path = str(tmp_path / "trades.csv")
    trade_id = journal.add_trade(_sample_trade(credit=1.35), path=path)

    pnl = journal.close_trade(trade_id, exit_debit=0.35, path=path)
    assert pnl == pytest.approx((1.35 - 0.35) * 100.0)

    df = journal.load_journal(path)
    row = df[df["id"] == trade_id].iloc[0]
    assert row["status"] == "CLOSED"
    assert row["realized_pnl"] == pytest.approx(100.0)


def test_close_trade_settle_underlying_between_short_strikes_is_max_profit(tmp_path):
    path = str(tmp_path / "trades.csv")
    trade_id = journal.add_trade(_sample_trade(credit=1.35), path=path)

    pnl = journal.close_trade(trade_id, settle_underlying=740.0, path=path)
    assert pnl == pytest.approx(1.35 * 100.0)


def test_close_trade_settle_underlying_beyond_wing_is_max_loss(tmp_path):
    path = str(tmp_path / "trades.csv")
    trade_id = journal.add_trade(_sample_trade(credit=1.35), path=path)

    pnl = journal.close_trade(trade_id, settle_underlying=800.0, path=path)  # far past long call
    assert pnl == pytest.approx((1.35 - 5.0) * 100.0)


def test_close_trade_requires_exactly_one_exit_method(tmp_path):
    path = str(tmp_path / "trades.csv")
    trade_id = journal.add_trade(_sample_trade(), path=path)

    with pytest.raises(ValueError):
        journal.close_trade(trade_id, path=path)  # neither given
    with pytest.raises(ValueError):
        journal.close_trade(trade_id, exit_debit=0.1, settle_underlying=750.0, path=path)  # both given


def test_close_trade_raises_for_unknown_id(tmp_path):
    path = str(tmp_path / "trades.csv")
    journal.add_trade(_sample_trade(), path=path)
    with pytest.raises(ValueError):
        journal.close_trade(999, exit_debit=0.1, path=path)


def test_close_trade_raises_if_already_closed(tmp_path):
    path = str(tmp_path / "trades.csv")
    trade_id = journal.add_trade(_sample_trade(), path=path)
    journal.close_trade(trade_id, exit_debit=0.1, path=path)
    with pytest.raises(ValueError):
        journal.close_trade(trade_id, exit_debit=0.1, path=path)


def test_list_open_filters_to_open_status_only(tmp_path):
    path = str(tmp_path / "trades.csv")
    id1 = journal.add_trade(_sample_trade(), path=path)
    id2 = journal.add_trade(_sample_trade(), path=path)
    journal.close_trade(id1, exit_debit=0.1, path=path)

    open_df = journal.list_open(path)
    assert list(open_df["id"]) == [id2]


def test_summarize_computes_win_rate_expectancy_and_drawdown(tmp_path):
    path = str(tmp_path / "trades.csv")
    win1 = journal.add_trade(_sample_trade(credit=1.35), path=path)
    win2 = journal.add_trade(_sample_trade(credit=1.35), path=path)
    loss1 = journal.add_trade(_sample_trade(credit=1.35), path=path)

    journal.close_trade(win1, exit_debit=0.35, path=path)   # +100
    journal.close_trade(win2, exit_debit=0.35, path=path)   # +100
    journal.close_trade(loss1, settle_underlying=800.0, path=path)  # -365

    df = journal.load_journal(path)
    summaries = journal.summarize(df)

    all_stats = summaries["All"]
    assert all_stats.num_trades == 3
    assert all_stats.win_rate_pct == pytest.approx(200.0 / 3, abs=0.01)
    assert all_stats.total_pnl == pytest.approx(100 + 100 - 365)
    assert all_stats.max_drawdown > 0

    assert "Weekly" in summaries
    assert summaries["Weekly"].num_trades == 3


def test_summarize_handles_no_closed_trades(tmp_path):
    path = str(tmp_path / "trades.csv")
    journal.add_trade(_sample_trade(), path=path)  # still open

    df = journal.load_journal(path)
    summaries = journal.summarize(df)
    assert summaries["All"].num_trades == 0


def test_render_journal_report_includes_open_and_closed_sections(tmp_path):
    path = str(tmp_path / "trades.csv")
    open_id = journal.add_trade(_sample_trade(), path=path)
    closed_id = journal.add_trade(_sample_trade(), path=path)
    journal.close_trade(closed_id, exit_debit=0.35, path=path)

    df = journal.load_journal(path)
    report_md = journal.render_journal_report(df)

    assert f"| {open_id} |" in report_md
    assert "Realized performance" in report_md
    assert "Weekly" in report_md


def test_cli_add_then_close_then_report(tmp_path, capsys):
    path = str(tmp_path / "trades.csv")
    journal.main([
        "--path", path, "add", "--label", "Weekly", "--expiration", "2026-10-09", "--dte", "8",
        "--short-call", "760", "--long-call", "765", "--short-put", "720", "--long-put", "715",
        "--credit", "1.35", "--entry-underlying", "740.5",
    ])
    out = capsys.readouterr().out
    assert "Logged trade id 1" in out

    journal.main(["--path", path, "close", "--id", "1", "--exit-debit", "0.35"])
    out = capsys.readouterr().out
    assert "realized P&L $100.00" in out

    journal.main(["--path", path, "report"])
    out = capsys.readouterr().out
    assert "Realized performance" in out

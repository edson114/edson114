"""Markdown and JSON for one run."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from typing import Optional

from .bars import bar_close_time
from .pipeline import Candidate, RunResult, rank

DISCLAIMER = (
    "> Signals only: nothing here places an order, and your trader config has "
    "`allow_options: {allow}`. The {target:.0f}% target is a goal, not a forecast: the odds below "
    "come from replaying the stock's own last {sessions} sessions, not from knowing today. "
    "No signal makes a profit certain; buying options can lose the whole premium."
)


def _clock(day: dt.date, slot: Optional[int]) -> str:
    if slot is None:
        return "-"
    return bar_close_time(day, slot).strftime("%-I:%M %p")


def _pct(x: float) -> str:
    return f"{x * 100:.1f} %"


def verdict(o, target: float) -> list:
    """One line from the replays' average return on the premium, plus a note
    when most replays never reached either exit."""
    avg = o.avg_return_pct
    size = abs(avg)
    way = "positive" if avg > 0 else "negative"
    if size < 3:
        line = f"Close to break-even: the replays average {avg:+.1f}% on the premium."
    elif size < 10:
        line = f"Leans {way}: the replays average {avg:+.1f}% on the premium."
    else:
        line = f"Strongly {way}: the replays average {avg:+.1f}% on the premium."
    lines = [line]
    if o.p_neither > 0.4:
        lines.append(
            f"{o.p_neither * 100:.1f}% of the replays reached neither the +{target:.0f}% target nor the stop; "
            "the price at the flat-by time decides those."
        )
    return lines


def _pick_block(c: Candidate, day: dt.date, target: float) -> list:
    s, k, o, h, z = c.setup, c.contract, c.odds, c.history, c.sizing
    action = "BUY CALL" if k.option_type == "call" else "BUY PUT"
    lines = [
        f"## {action}: {c.symbol} {k.expiration} {k.strike:g} {k.option_type}",
        "",
        f"- **Setup:** the {_clock(day, s.trigger_slot)} close {s.trigger_price:.2f} broke the 15-minute range "
        f"{s.or_low:.2f}-{s.or_high:.2f} {'up' if k.option_type == 'call' else 'down'}; latest close "
        f"{s.last_close:.2f} ({_clock(day, s.last_slot)}).",
        f"- **Contract:** bid {k.bid:.2f} / ask {k.ask:.2f} (spread {k.spread_pct:.1f}% of mid), "
        f"{k.dte} days to expiry, delta {k.delta:+.2f}, implied vol {k.iv * 100:.1f}% (solved from the mid), "
        f"open interest {k.open_interest if k.open_interest is not None else 'n/a'}.",
        f"- **Pay** the ask {k.ask:.2f}. **Target** the bid at {k.ask * (1 + target / 100):.2f} (+{target:.0f}%)"
        + (f", about {c.symbol} {c.spot_needed:.2f} right now (further later in the day as time value decays)."
           if c.spot_needed else "."),
        f"- **Stop:** {c.symbol} at {s.stop:.2f} (the range midpoint); at today's volatility the option would "
        f"sell for about {o.return_at_stop_pct:+.0f}% there. **Out by 3:55 PM**, win or lose.",
        "",
        f"{o.replays:,} replays of the rest of the session ({o.bars_left} five-minute bars), seed {o.seed}:",
        "",
        "| outcome | odds |",
        "|---|---|",
        f"| +{target:.0f}% target first | {_pct(o.p_target)} |",
        f"| stop first | {_pct(o.p_stop)} |",
        f"| neither, sold at 3:55 PM | {_pct(o.p_neither)} |",
        f"| green (any gain) | {_pct(o.p_green)} |",
        "",
        f"Return on the premium: average {o.avg_return_pct:+.1f}%, median {o.median_return_pct:+.1f}%, "
        f"90% of replays between {o.p5_return_pct:+.1f}% and {o.p95_return_pct:+.1f}%.",
        "",
        *[f"**{v}**" for v in verdict(o, target)],
        "",
    ]
    if h is not None and h.triggered:
        lines.append(
            f"History: the same rule on {h.sessions} real sessions fired {h.triggered} times: {h.target} target, "
            f"{h.stop} stop, {h.neither} neither; average {h.avg_return_pct:+.1f}% on the premium "
            "(the stock's real path; the option priced at today's volatility)."
        )
        lines.append("")
    if z is not None:
        lines.append(
            f"Size: **{z.contracts} contract{'s' if z.contracts != 1 else ''}** "
            f"(${z.cost_per_contract:,.0f} each, about ${z.loss_per_contract:,.0f} lost per contract at the stop; "
            f"bound by your {z.binding})." + (f" {z.note}" if z.note else "")
        )
        lines.append("")
    for f in c.flags:
        lines.append(f"- {f}")
    if c.flags:
        lines.append("")
    return lines


def render_markdown(r: RunResult) -> str:
    day = r.now.date()
    target = r.cfg.profit_target_pct
    sessions = max((c.sessions for c in r.candidates), default=0)
    out = [
        f"# Options Signal -- {r.now:%a %Y-%m-%d %H:%M} ET",
        "",
        DISCLAIMER.format(allow=str(bool(r.trader.get("allow_options"))).lower(), target=target, sessions=sessions or "~59"),
        "",
    ]
    if r.vix is not None:
        out += [f"VIX {r.vix:.1f}.", ""]
    if r.status in ("closed", "early"):
        out += [f"**{r.message}**", ""]
        return "\n".join(out)
    if r.message:
        out += [f"**{r.message}**", ""]

    if r.pick:
        out += _pick_block(r.pick, day, target)

    ranked = rank(r.candidates)
    if ranked:
        out += [
            "## Every triggered setup, most likely to reach the target first",
            "",
            "| | ticker | option | trigger | entry | stop | +target first | stop first | neither | avg return | history (T/S/N) | contracts |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for i, c in enumerate(ranked, 1):
            k, o, h, s = c.contract, c.odds, c.history, c.setup
            hist = f"{h.target}/{h.stop}/{h.neither}" if h and h.triggered else "-"
            mark = " (late)" if c.late else ""
            out.append(
                f"| {i} | {c.symbol} | {k.option_type.upper()} {k.strike:g} {k.expiration} @ {k.ask:.2f} | "
                f"{_clock(day, s.trigger_slot)}{mark} | {s.last_close:.2f} | {s.stop:.2f} | {_pct(o.p_target)} | "
                f"{_pct(o.p_stop)} | {_pct(o.p_neither)} | {o.avg_return_pct:+.1f}% | {hist} | {c.sizing.contracts} |"
            )
        out.append("")
        flagged = [c for c in ranked if c is not r.pick and c.flags]
        if flagged:
            out += ["Flags:", ""]
            out += [f"- {c.symbol}: " + " ".join(c.flags) for c in flagged]
            out.append("")

    waiting = [c for c in r.candidates if c.setup and c.setup.status in ("waiting", "faded", "forming")]
    if waiting:
        out += ["## Not triggered", ""]
        for c in waiting:
            s = c.setup
            rng = f" range {s.or_low:.2f}-{s.or_high:.2f}" if s.status != "forming" else ""
            out.append(f"- {c.symbol}: {s.status}{rng}" + (f" ({s.note})" if s.note else ""))
        out.append("")

    skipped = [c for c in r.candidates if c.skip_reason]
    if skipped:
        out += ["## Skipped", ""]
        out += [f"- {c.symbol}: {c.skip_reason}" for c in skipped]
        out.append("")

    out += [
        "## How this works",
        "",
        "- Setup (trader-scanner / trader-entry): the first 5-minute close beyond the 9:30-9:45 range; "
        "above it is a call, below it a put. Evaluated from the latest close, what you would pay now.",
        "- Exit (trader-exit): stop at the range midpoint on the stock, out by the flat-by time.",
        "- Odds (trader-timemachine): 10,000 replays from the stock's own 5-minute bars at the same time of "
        "day over its last ~60 days, the option re-priced each bar with Black-Scholes at today's implied "
        "volatility. Volatility changes, earnings and news are not in those replays.",
        "- Cost: you pay the ask and sell at the bid, so the spread counts against every trade.",
        "- Size (trader-budget / trader-risk): your max loss per trade, position cap and cash buffer, in contracts.",
        "- Gates: the repo's FOMC/CPI calendar and VIX threshold skip the day; earnings today skips the stock.",
        "",
        "Free Yahoo data is unofficial and can lag. Check live quotes in your broker before acting.",
    ]
    return "\n".join(out) + "\n"


def to_json(r: RunResult) -> dict:
    def cand(c: Candidate) -> dict:
        d = {
            "symbol": c.symbol,
            "skip_reason": c.skip_reason,
            "flags": c.flags,
            "late": c.late,
            "sessions": c.sessions,
            "spot_needed": c.spot_needed,
        }
        for name in ("setup", "contract", "odds", "history", "sizing"):
            v = getattr(c, name)
            d[name] = asdict(v) if v is not None else None
        return d

    return {
        "created_by": "options_signal",
        "now": r.now.isoformat(timespec="minutes"),
        "status": r.status,
        "message": r.message,
        "gates": r.gates,
        "vix": r.vix,
        "target_pct": r.cfg.profit_target_pct,
        "pick": cand(r.pick) if r.pick else None,
        "ranked": [cand(c) for c in rank(r.candidates)],
        "candidates": [cand(c) for c in r.candidates],
    }

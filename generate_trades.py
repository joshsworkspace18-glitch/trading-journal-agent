#!/usr/bin/env python3
"""
Synthetic trade-journal generator for the Trading Journal Agent.

Creates ~120 trades for one persona ("Ravi") over ~6 weeks, with behaviour
patterns PLANTED on purpose so you can verify the agent finds them:

  1. revenge      - re-enters within 5-30 min of a losing trade, bigger size, mostly loses
  2. fomo         - jumps in after a big move, mostly loses
  3. friday_extra - overtrades on Friday afternoons, mostly loses
  4. planned      - patient setup trades with a stop-loss, mostly wins

IMPORTANT: the pattern label ("revenge", "fomo", ...) is written ONLY to the
ground-truth CSV. It is NOT put into the text that goes into Hindsight, so the
agent has to discover the patterns from behaviour + notes, not read a label.

Usage:
    python generate_trades.py                 # writes ./data/*
    python generate_trades.py --seed 11 --holdout 12   # seed 11 is the default

Outputs (in ./data):
    history.json        trades to load into Hindsight (all except the holdout)
    demo_trades.json    the last N trades, kept back for the LIVE demo
    trades_ground_truth.csv   every trade with its hidden pattern label
    summary.txt         win rate / P&L by pattern (your evaluation numbers)

All data is synthetic. Prices, tickers and P&L are made up.
"""
import argparse
import csv
import json
import os
import random
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
TRADER = "Ravi"

# ------------------------------------------------------------------ setup
INDEX_UNDERLYINGS = [("NIFTY", 24500, 50, 75), ("BANKNIFTY", 52500, 100, 30)]
STOCKS = [("RELIANCE", 1450), ("HDFCBANK", 1720), ("INFY", 1580), ("TATAMOTORS", 780)]

PLANNED_SETUPS = [
    "breakout above previous day high",
    "pullback to the 20 EMA on the 15-minute chart",
    "opening range breakout after 9:45",
    "support bounce with volume confirmation",
]

NOTES = {
    "planned": [
        "Planned setup: {setup}. Waited for confirmation and placed the stop-loss before entering.",
        "Clean {setup}. Sized normally, stop-loss set at {sl}.",
        "Followed my checklist: {setup}, risk defined up front, stop-loss at {sl}.",
    ],
    "revenge": [
        "Just got stopped out. Need to win that back, going in again right away.",
        "Angry about the last loss, re-entering immediately and taking a bigger quantity.",
        "Can't accept that loss. Same direction again, doubling up to recover faster.",
        "Market took my money, I'm taking it back. No time to wait for a setup.",
    ],
    "fomo": [
        "Missed the earlier move and it is still running, jumping in so I don't miss more.",
        "Big green candle and everyone is talking about it, entering late.",
        "Saw it up strongly and felt I had to get in now before it goes further.",
        "Kept watching it run without me, finally gave in and bought the breakout candle.",
    ],
    "friday_extra": [
        "Friday afternoon, choppy market, but I want to end the week in green.",
        "Bored, no real setup, taking one more trade before close.",
        "Week was mixed, trying to squeeze one more trade in before the weekend.",
    ],
}

MOODS = {
    "planned": ["calm", "focused", "confident", "patient"],
    "revenge": ["angry", "frustrated", "anxious", "irritated"],
    "fomo": ["excited", "anxious", "impatient"],
    "friday_extra": ["restless", "bored", "impatient"],
}

WIN_PROB = {"planned": 0.65, "revenge": 0.15, "fomo": 0.22, "friday_extra": 0.22}
STOP_LOSS_PROB = {"planned": 0.92, "revenge": 0.15, "fomo": 0.35, "friday_extra": 0.30}


# ------------------------------------------------------------------ helpers
def trading_days(start: date, n_weeks: int):
    d = start
    end = start + timedelta(weeks=n_weeks)
    while d < end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def at(day: date, hh: int, mm: int) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


def pick_instrument(rng: random.Random):
    """Return (name, entry_price, quantity_unit)."""
    if rng.random() < 0.65:
        und, base, step, lot = rng.choice(INDEX_UNDERLYINGS)
        strike = base + rng.choice([-2, -1, 0, 1, 2]) * step
        opt = rng.choice(["CE", "PE"])
        premium = round(rng.uniform(55, 230), 2)
        return f"{und} {strike} {opt}", premium, lot
    name, px = rng.choice(STOCKS)
    px = round(px * rng.uniform(0.97, 1.03), 2)
    return name, px, rng.choice([10, 20, 25, 50])


def make_trade(rng, kind, entry_dt, prev):
    instrument, entry_price, unit = pick_instrument(rng)
    lots = rng.choice([1, 1, 2])
    size_mult = 1.0
    if kind == "revenge":
        lots = max(2, lots * 2)  # doubles up
        size_mult = 1.4
    qty = lots * unit
    won = rng.random() < WIN_PROB[kind]
    used_sl = rng.random() < STOP_LOSS_PROB[kind]

    if won:
        pnl = rng.uniform(900, 4200) * size_mult
    else:
        # skipping the stop-loss makes losses bigger
        pnl = -rng.uniform(500, 2200) * (1.0 if used_sl else 1.8) * size_mult
    pnl = round(pnl / 10) * 10
    # keep exit price positive and sane
    floor = -0.92 * entry_price * qty
    pnl = max(pnl, round(floor / 10) * 10)
    exit_price = round(entry_price + pnl / qty, 2)

    hold_min = rng.randint(8, 55) if kind != "planned" else rng.randint(20, 90)
    exit_dt = min(entry_dt + timedelta(minutes=hold_min), at(entry_dt.date(), 15, 25))

    sl_level = round(entry_price * rng.uniform(0.82, 0.92), 2)
    setup = rng.choice(PLANNED_SETUPS)
    note = rng.choice(NOTES[kind]).format(setup=setup, sl=sl_level)
    mood = rng.choice(MOODS[kind])

    return {
        "instrument": instrument,
        "qty": qty,
        "entry_time": entry_dt,
        "exit_time": exit_dt,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl": pnl,
        "result": "WIN" if pnl > 0 else "LOSS",
        "stop_loss_used": used_sl,
        "mood": mood,
        "note": note,
        "pattern": kind,  # hidden label, ground truth only
    }


def build_history(seed: int, weeks: int = 6, start=date(2026, 8, 17)):
    rng = random.Random(seed)
    trades = []

    for day in trading_days(start, weeks):
        day_trades = []

        # 1-3 patient, planned trades spread through the morning/midday
        n_planned = rng.choice([1, 2, 2, 3])
        slots = sorted(rng.sample(range(9 * 60 + 35, 13 * 60), n_planned))
        for m in slots:
            day_trades.append(make_trade(rng, "planned", at(day, m // 60, m % 60), None))

        # occasional FOMO entry
        if rng.random() < 0.45:
            m = rng.randint(10 * 60, 14 * 60)
            day_trades.append(make_trade(rng, "fomo", at(day, m // 60, m % 60), None))

        # Friday afternoon overtrading
        if day.weekday() == 4:
            for _ in range(rng.choice([2, 3])):
                m = rng.randint(13 * 60 + 35, 15 * 60 + 5)
                day_trades.append(make_trade(rng, "friday_extra", at(day, m // 60, m % 60), None))

        day_trades.sort(key=lambda t: t["entry_time"])

        # walk the day: after a loss, revenge re-entry is likely
        walked = []
        for t in day_trades:
            walked.append(t)
            if t["result"] == "LOSS" and rng.random() < 0.65:
                gap = rng.randint(5, 30)
                r_entry = t["exit_time"] + timedelta(minutes=gap)
                if r_entry <= at(day, 15, 10):
                    walked.append(make_trade(rng, "revenge", r_entry, t))
        walked.sort(key=lambda t: t["entry_time"])
        trades.extend(walked)

    # number them and attach "previous trade" facts the app itself could compute
    for i, t in enumerate(trades, start=1):
        t["trade_id"] = f"trade_{i:03d}"
        if i > 1:
            p = trades[i - 2]
            gap = int((t["entry_time"] - p["exit_time"]).total_seconds() // 60)
            if p["exit_time"].date() == t["entry_time"].date() and gap >= 0:
                t["prev_result"] = p["result"]
                t["prev_pnl"] = p["pnl"]
                t["mins_since_prev_exit"] = gap
    return trades


def to_memory_text(t):
    """The text that goes into Hindsight. NOTE: no pattern label in here."""
    when = t["entry_time"].strftime("%A %d %B %Y, %H:%M IST")
    parts = [
        f"Trade {t['trade_id']}: {TRADER} bought {t['instrument']} (quantity {t['qty']}) "
        f"at {t['entry_price']} on {when}.",
        f"Reason given: {t['note']}",
        f"Mood: {t['mood']}.",
        f"Stop-loss used: {'yes' if t['stop_loss_used'] else 'no'}.",
    ]
    if "prev_result" in t:
        parts.append(
            f"His previous trade earlier the same day was a {t['prev_result'].lower()} "
            f"({t['prev_pnl']:+} rupees) and had closed {t['mins_since_prev_exit']} minutes before this entry."
        )
    parts.append(
        f"Outcome: closed at {t['exit_time'].strftime('%H:%M')} at {t['exit_price']}, "
        f"P&L {t['pnl']:+} rupees ({t['result']})."
    )
    return " ".join(parts)


def serialise(t):
    return {
        "trade_id": t["trade_id"],
        "timestamp": t["entry_time"].isoformat(),
        "context": "trade journal entry and outcome",
        "content": to_memory_text(t),
    }


def summarise(trades):
    lines = ["Ground-truth summary (synthetic data)", "=" * 40]
    for kind in ["planned", "revenge", "fomo", "friday_extra"]:
        ts = [t for t in trades if t["pattern"] == kind]
        if not ts:
            continue
        wins = sum(1 for t in ts if t["result"] == "WIN")
        pnl = sum(t["pnl"] for t in ts)
        lines.append(
            f"{kind:13s} trades={len(ts):3d}  win_rate={wins/len(ts):5.0%}  net_pnl={pnl:+8d}"
        )
    tot = sum(t["pnl"] for t in trades)
    lines.append(f"{'ALL':13s} trades={len(trades):3d}  net_pnl={tot:+8d}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--weeks", type=int, default=6)
    ap.add_argument("--holdout", type=int, default=10,
                    help="last N trades kept out of the bank for the live demo")
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    trades = build_history(args.seed, args.weeks)
    split = len(trades) - args.holdout
    hist, demo = trades[:split], trades[split:]

    with open(os.path.join(args.out, "history.json"), "w") as f:
        json.dump([serialise(t) for t in hist], f, indent=2)
    with open(os.path.join(args.out, "demo_trades.json"), "w") as f:
        json.dump([serialise(t) for t in demo], f, indent=2)

    cols = ["trade_id", "entry_time", "exit_time", "instrument", "qty", "entry_price",
            "exit_price", "pnl", "result", "stop_loss_used", "mood", "pattern", "note"]
    with open(os.path.join(args.out, "trades_ground_truth.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for t in trades:
            w.writerow({**t, "entry_time": t["entry_time"].isoformat(),
                        "exit_time": t["exit_time"].isoformat()})

    summary = summarise(trades)
    with open(os.path.join(args.out, "summary.txt"), "w") as f:
        f.write(summary + "\n")

    print(f"Generated {len(trades)} trades: {len(hist)} for history, {len(demo)} held out for the live demo")
    print(summary)
    print("\nSample memory text:\n" + to_memory_text(trades[5]))


if __name__ == "__main__":
    main()

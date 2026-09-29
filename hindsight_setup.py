#!/usr/bin/env python3
"""
Set up the Hindsight memory bank for the Trading Journal Agent, load the
synthetic history, and run pre-trade checks.

Install:   pip install hindsight-client
Config:    export HINDSIGHT_URL=https://api.hindsight.vectorize.io   (or http://localhost:8888)
           export HINDSIGHT_API_KEY=...                              (Cloud only)

Commands:
    python hindsight_setup.py setup                 # create bank + mission + disposition + directive
    python hindsight_setup.py load                  # retain data/history.json into the bank
    python hindsight_setup.py check "your plan"     # pre-trade check, prints answer + memories used
    python hindsight_setup.py log-demo 1            # retain the Nth held-out demo trade live

Use --bank to change the bank id (default: trader-ravi). Use a different bank id
(e.g. trader-empty) with the same `check` command to show the "memory OFF" case.
"""
import argparse
import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from hindsight_client import Hindsight

load_dotenv()

from hindsight_client import Hindsight

DEFAULT_BANK = "trader-ravi"

REFLECT_MISSION = (
    "You are a trading behaviour coach for one trader. Your job is to identify "
    "patterns from this trader's own trade history: what he did, how he felt, "
    "whether he used a stop-loss, and what happened afterwards. Be direct, specific, "
    "and evidence-based. Cite concrete past trades with dates, quantities, and outcomes "
    "when available. Compare planned trades against impulsive or recovery-driven trades. "
    "Do not invent facts or assume that a pattern applies to every trade. "
    "Avoid absolute claims such as 'every time', 'always', 'never', or 'invariably' "
    "unless the retrieved history clearly supports them. If there are exceptions, "
    "acknowledge them. Distinguish between a recurring pattern and individual examples. "
    "When discussing outcomes, report what the historical trades actually show rather "
    "than treating correlation as causation. Do not give guaranteed predictions about "
    "future trading results. Focus on helping the trader recognize behaviour using "
    "their own documented history."
)

DIRECTIVE_TEXT = (
    "Never recommend specific stocks or instruments, and never predict prices or market "
    "direction. Only discuss the trader's own behaviour and history. This is a behaviour "
    "journal, not financial advice."
)


def client() -> Hindsight:
    url = os.getenv("HINDSIGHT_URL", "http://localhost:8888")
    key = os.getenv("HINDSIGHT_API_KEY")
    return Hindsight(base_url=url, api_key=key)


def cmd_setup(c: Hindsight, bank: str):
    c.create_bank(bank_id=bank)
    c.update_bank_config(
        bank,
        reflect_mission=REFLECT_MISSION,
        disposition_skepticism=4,   # pushes back instead of cheering the trader on
        disposition_empathy=2,
    )
    c.create_directive(bank_id=bank, name="no-financial-advice", content=DIRECTIVE_TEXT, priority=10)
    print(f"Bank '{bank}' ready.")


def cmd_load(c: Hindsight, bank: str, path: str, use_async: bool):
    with open(path) as f:
        trades = json.load(f)
    for i, t in enumerate(trades, start=1):
        c.retain(
            bank_id=bank,
            content=t["content"],
            context=t["context"],
            timestamp=datetime.fromisoformat(t["timestamp"]),
            document_id=t["trade_id"],   # same id later = upsert, so outcomes can update an entry
            retain_async=use_async,
        )
        print(f"retained {t['trade_id']} ({i}/{len(trades)})", flush=True)
    print("Done. Memory extraction can take a little while; check the bank in the UI before demoing.")


def cmd_check(c: Hindsight, bank: str, plan: str):
    resp = c.reflect(bank_id=bank, query=plan, budget="mid", include_facts=True)
    print("\n=== COACH ===\n")
    print(resp.text)
    based_on = getattr(resp, "based_on", None)
    mems = (based_on.memories or []) if based_on else []
    print(f"\n=== MEMORIES USED ({len(mems)}) ===")
    for m in mems:
        print("-", m.text)


def cmd_log_demo(c: Hindsight, bank: str, n: int, path: str):
    with open(path) as f:
        trades = json.load(f)
    t = trades[n - 1]
    c.retain(
        bank_id=bank,
        content=t["content"],
        context=t["context"],
        timestamp=datetime.fromisoformat(t["timestamp"]),
        document_id=t["trade_id"],
    )
    print(f"Logged live: {t['trade_id']}\n{t['content']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["setup", "load", "check", "log-demo"])
    ap.add_argument("arg", nargs="?", help="plan text for `check`, or index for `log-demo`")
    ap.add_argument("--bank", default=DEFAULT_BANK)
    ap.add_argument("--history", default="data/history.json")
    ap.add_argument("--demo", default="data/demo_trades.json")
    ap.add_argument("--async", dest="use_async", action="store_true",
                    help="queue retains without waiting (faster bulk load)")
    a = ap.parse_args()

    c = client()
    if a.command == "setup":
        cmd_setup(c, a.bank)
    elif a.command == "load":
        cmd_load(c, a.bank, a.history, a.use_async)
    elif a.command == "check":
        if not a.arg:
            sys.exit('Give the plan text, e.g. check "I just got stopped out, want to re-enter NIFTY"')
        cmd_check(c, a.bank, a.arg)
    elif a.command == "log-demo":
        cmd_log_demo(c, a.bank, int(a.arg or 1), a.demo)


if __name__ == "__main__":
    main()

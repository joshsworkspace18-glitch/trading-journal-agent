"""
Trading Journal Agent - Streamlit UI (skeleton for the UI owner)

Run:
    pip install streamlit pandas hindsight-client
    export HINDSIGHT_URL=...        # Cloud URL or http://localhost:8888
    export HINDSIGHT_API_KEY=...    # Cloud only
    streamlit run app.py

Prerequisite: run  `python hindsight_setup.py setup` and `load` first
(and `setup --bank trader-empty` for the "memory OFF" bank).

Tabs
    1. Pre-trade check   -> reflect() over the trader's bank, with the memories it used
    2. Log a trade       -> retain() new entries / outcomes (this is the "it keeps learning" moment)
    3. What it remembers -> recall() search over the bank
    4. Behaviour cost    -> evaluation chart from the synthetic ground-truth CSV

TODO markers (search for "TODO") show where the UI owner can polish.
"""
import json
import os
from dotenv import load_dotenv
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
from hindsight_client import Hindsight

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))
BANK_ON = os.getenv("BANK_ON", "trader-ravi")
BANK_OFF = os.getenv("BANK_OFF", "trader-empty")
DATA = Path(os.getenv("DATA_DIR", "data"))

st.set_page_config(page_title="Trading Journal Agent", page_icon="📓", layout="wide")


# ------------------------------------------------------------------ backend helpers
@contextmanager
def hindsight():
    """A fresh client per call.

    Streamlit reruns the script on a new thread for every interaction, and the
    Hindsight client binds its HTTP session to the event loop it was created on.
    Reusing one cached client therefore fails with "Event loop is closed" on the
    second interaction. Creating a short-lived client per call avoids that.
    """
    c = Hindsight(
       base_url=os.getenv("HINDSIGHT_URL", "http://localhost:8888"),
api_key=os.getenv("HINDSIGHT_API_KEY"),
    )
    try:
        yield c
    finally:
        try:
            c.close()
        except Exception:
            pass


def pre_trade_check(bank: str, plan: str):
    """reflect() returns (answer_text, [memory_texts_used])."""
    with hindsight() as c:
        resp = c.reflect(bank_id=bank, query=plan, budget="mid", include_facts=True)
    based_on = getattr(resp, "based_on", None)
    mems = [m.text for m in ((based_on.memories or []) if based_on else [])]
    return resp.text, mems


def search_memories(bank: str, query: str):
    with hindsight() as c:
        resp = c.recall(bank_id=bank, query=query, budget="mid")
    return resp.results


def retain_trade(bank: str, trade_id: str, content: str, when: datetime):
    with hindsight() as c:
        c.retain(
            bank_id=bank,
            content=content,
            context="trade journal entry and outcome",
            timestamp=when,
            document_id=trade_id,  # same id again = upsert (used when recording the outcome)
        )


def entry_text(t: dict) -> str:
    when = t["time"].strftime("%A %d %B %Y, %H:%M IST")
    text = (
        f"Trade {t['id']}: Ravi bought {t['instrument']} (quantity {t['qty']}) at {t['price']} on {when}. "
        f"Reason given: {t['reason']} Mood: {t['mood']}. "
        f"Stop-loss used: {'yes' if t['stop_loss'] else 'no'}."
    )
    if t.get("prev"):
        text += f" {t['prev']}"
    return text


# ------------------------------------------------------------------ session state
if "open_trades" not in st.session_state:
    st.session_state.open_trades = {}   # trade_id -> dict (so we can add the outcome later)
if "last_closed" not in st.session_state:
    st.session_state.last_closed = None  # (result, pnl, closed_at) to auto-fill "previous trade"


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("📓 Trading Journal Agent")
    st.caption("A behaviour coach that remembers your own trades. Powered by Hindsight memory.")
    memory_on = st.toggle("🧠 Memory ON", value=True,
                          help="OFF uses an empty bank, so the agent has no history to learn from.")
    bank = BANK_ON if memory_on else BANK_OFF
    st.write(f"Active bank: `{bank}`")
    st.divider()
    st.warning("Behaviour journal, not financial advice. All data in this demo is synthetic.")
    # TODO: add a small "bank stats" block here (memory count) if time allows


# ------------------------------------------------------------------ tabs
tab_check, tab_log, tab_mem, tab_cost = st.tabs(
    ["🛑 Pre-trade check", "📝 Log a trade", "🧠 What it remembers", "📉 Behaviour cost"]
)

# ---- 1. pre-trade check ---------------------------------------------------
with tab_check:
    st.subheader("About to place a trade? Check in first.")
    examples = [
        "I just got stopped out on NIFTY. I want to re-enter right away with double the quantity.",
        "It's Friday afternoon, I'm up a little for the week, thinking of one more trade before close.",
        "This stock is up 5% and everyone is talking about it. I want to buy now before it runs more.",
    ]
    ex = st.selectbox("Try an example", ["(write your own)"] + examples)
    plan = st.text_area("Your plan", value="" if ex == "(write your own)" else ex, height=110,
                        placeholder="What are you about to do, and why?")

    c1, c2 = st.columns(2)
    run_one = c1.button("Check with the active bank", type="primary", disabled=not plan.strip())
    run_cmp = c2.button("⚖️ Compare: memory OFF vs ON", disabled=not plan.strip())

    if run_one:
        try:
            with st.spinner("Checking your history..."):
                answer, mems = pre_trade_check(bank, plan)
            st.markdown("### Coach")
            st.markdown(answer)
            with st.expander(f"Memories used ({len(mems)})", expanded=True):
                for m in mems:
                    st.markdown(f"- {m}")
                if not mems:
                    st.info("No memories were used for this answer.")
        except Exception as e:  # TODO: friendlier error states
            st.error(f"Could not reach Hindsight: {e}")

    if run_cmp:
        left, right = st.columns(2)
        for col, label, b in ((left, "🚫 Memory OFF", BANK_OFF), (right, "🧠 Memory ON", BANK_ON)):
            with col:
                st.markdown(f"#### {label}")
                try:
                    with st.spinner("Thinking..."):
                        answer, mems = pre_trade_check(b, plan)
                    st.markdown(answer)
                    with st.expander(f"Memories used ({len(mems)})", expanded=(b == BANK_ON)):
                        for m in mems:
                            st.markdown(f"- {m}")
                except Exception as e:
                    st.error(f"Error: {e}")


# ---- 2. log a trade -------------------------------------------------------
with tab_log:
    st.subheader("Log a trade")
    st.caption("Logging retains the entry in memory. Recording the outcome updates the same entry.")

    with st.form("entry_form", clear_on_submit=True):
        a, b_, c = st.columns(3)
        instrument = a.text_input("Instrument", "NIFTY 24500 CE")
        qty = b_.number_input("Quantity", min_value=1, value=75, step=1)
        price = c.number_input("Entry price", min_value=0.05, value=120.0, step=0.05)
        reason = st.text_area("Why are you taking this trade?", height=80)
        d, e = st.columns(2)
        mood = d.selectbox("Mood", ["calm", "focused", "confident", "excited", "anxious",
                                    "frustrated", "angry", "bored", "restless"])
        stop_loss = e.checkbox("Stop-loss set", value=True)
        submitted = st.form_submit_button("Log entry")

    if submitted and reason.strip():
        now = datetime.now(IST)
        tid = f"live_{now.strftime('%Y%m%d_%H%M%S')}"
        prev = None
        if st.session_state.last_closed:
            res, pnl, closed_at = st.session_state.last_closed
            mins = int((now - closed_at).total_seconds() // 60)
            prev = (f"His previous trade earlier the same day was a {res.lower()} ({pnl:+} rupees) "
                    f"and had closed {mins} minutes before this entry.")
        t = dict(id=tid, instrument=instrument, qty=int(qty), price=price, reason=reason.strip(),
                 mood=mood, stop_loss=stop_loss, time=now, prev=prev)
        try:
            retain_trade(BANK_ON, tid, entry_text(t), now)
            st.session_state.open_trades[tid] = t
            st.success(f"Logged {tid}")
        except Exception as ex_:
            st.error(f"Could not save to memory: {ex_}")

    if st.session_state.open_trades:
        st.markdown("#### Open trades")
        for tid, t in list(st.session_state.open_trades.items()):
            with st.container(border=True):
                st.write(f"**{tid}**: {t['instrument']} x{t['qty']} @ {t['price']}")
                x, y, z = st.columns([1, 1, 1])
                exit_price = x.number_input("Exit price", min_value=0.05, value=float(t["price"]),
                                            step=0.05, key=f"exit_{tid}")
                if z.button("Record outcome", key=f"close_{tid}"):
                    now = datetime.now(IST)
                    pnl = int(round((exit_price - t["price"]) * t["qty"]))
                    res = "WIN" if pnl > 0 else "LOSS"
                    content = (entry_text(t) + f" Outcome: closed at {now.strftime('%H:%M')} at "
                               f"{exit_price}, P&L {pnl:+} rupees ({res}).")
                    try:
                        retain_trade(BANK_ON, tid, content, t["time"])
                        st.session_state.last_closed = (res, pnl, now)
                        del st.session_state.open_trades[tid]
                        st.rerun()
                    except Exception as ex_:
                        st.error(f"Could not save outcome: {ex_}")

    st.divider()
    st.markdown("#### Demo shortcut: add a held-out trade")
    demo_path = DATA / "demo_trades.json"
    if demo_path.exists():
        demo = json.loads(demo_path.read_text())
        idx = st.number_input("Demo trade number", min_value=1, max_value=len(demo), value=1)
        st.caption(demo[idx - 1]["content"])
        if st.button("Retain this demo trade"):
            t = demo[idx - 1]
            try:
                retain_trade(BANK_ON, t["trade_id"], t["content"], datetime.fromisoformat(t["timestamp"]))
                st.success(f"Retained {t['trade_id']}")
            except Exception as ex_:
                st.error(f"Error: {ex_}")
    else:
        st.info("Run generate_trades.py first to create data/demo_trades.json")


# ---- 3. what it remembers --------------------------------------------------
with tab_mem:
    st.subheader("What does the agent remember?")
    q = st.text_input("Search the bank", "times I re-entered right after a loss")
    if st.button("Search memory") and q.strip():
        try:
            results = search_memories(bank, q)
            st.caption(f"{len(results)} memories from `{bank}`")
            for r in results:
                with st.container(border=True):
                    st.write(r.text)
                    meta = " | ".join(x for x in [r.type, r.occurred_start] if x)
                    if meta:
                        st.caption(meta)
        except Exception as e:
            st.error(f"Could not reach Hindsight: {e}")
    # TODO: show a timeline or count of memories by week


# ---- 4. behaviour cost (evaluation view) -----------------------------------
with tab_cost:
    st.subheader("What the emotional trades cost")
    st.caption("Evaluation view. Uses the hidden pattern labels from the synthetic dataset. "
               "The agent never sees these labels.")
    gt = DATA / "trades_ground_truth.csv"
    if gt.exists():
        df = pd.read_csv(gt)
        g = df.groupby("pattern").agg(trades=("pnl", "size"),
                                      net_pnl=("pnl", "sum"),
                                      win_rate=("result", lambda s: (s == "WIN").mean())).reset_index()
        m1, m2 = st.columns(2)
        m1.bar_chart(g.set_index("pattern")["net_pnl"])
        m1.caption("Net P&L by pattern (rupees, synthetic)")
        m2.bar_chart(g.set_index("pattern")["win_rate"])
        m2.caption("Win rate by pattern")
        st.dataframe(g)
        # TODO: once you have measured results from real `check` runs, add a
        # "flagged trades that would have lost money" chart here (label as synthetic).
    else:
        st.info("Run generate_trades.py first to create data/trades_ground_truth.csv")

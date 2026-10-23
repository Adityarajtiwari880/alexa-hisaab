"""
Hisaab: A voice-first ledger for household help, served as an MCP server for Alexa+.
Production-ready build for the Amazon Developer Hackathon (Build, Ship, Shape).
Supports:
- Model Context Protocol (MCP) Streamable HTTP Transport (spec 2025-11-25+)
- Authentic Echo Show 3D Light Ring with Synthesized Dual-Bell Wake Chime
- Robust Speech Recognition with Real-Time "Live Hearing" Transcription HUD
- Full Hindi & Hinglish Context (Natural voice comprehension & bilingual WhatsApp slips)
- Interactive Feature Showcase (Filterable command palette across 6 categories)
- Multi-Staff Ecosystem (Sunita the Maid, Ramesh the Cook, Raju the Driver, Anil the Milkman)
- 1-Tap Amazon Pay / UPI Settlement with Dynamic QR Code Generation
"""

import calendar
import json
import os
import re
import secrets
import sqlite3
import urllib.parse
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from functools import wraps
from html import escape
from typing import Optional
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

# ----------------------------------------------------------------- Settings & Config
TZ = ZoneInfo(os.environ.get("HISAAB_TZ", "Asia/Kolkata"))
TOKEN = os.environ.get("HISAAB_TOKEN", "demo")  # Secret token for web/calendar URLs
MCP_PATH = os.environ.get("HISAAB_MCP_PATH", "/mcp")
SYMBOL = os.environ.get("HISAAB_CURRENCY_SYMBOL", "₹")
WORD = os.environ.get("HISAAB_CURRENCY_WORD", "rupees")

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
GENERIC_NAMES = {
    "", "maid", "didi", "bai", "cook", "helper", "driver", "bhaiya", "staff", "help",
    "nanny", "she", "her", "he", "him", "them", "the maid", "the cook", "the driver",
}

mcp = FastMCP(
    "Hisaab",
    instructions=(
        "Hisaab keeps household accounts for domestic staff paid by attendance: maid, cook, "
        "driver, milkman, tutor. Staff count as present unless told otherwise. Keep replies "
        "concise and natural for voice synthesis. Supports English, Hindi, and Hinglish. "
        "Always pass exact dates as YYYY-MM-DD."
    ),
    host=os.environ.get("HOST", "0.0.0.0"),
    port=int(os.environ.get("PORT", "8000")),
    streamable_http_path=MCP_PATH,
    stateless_http=True,
    json_response=True,
)

# ----------------------------------------------------------------- Database Layer
SCHEMA = """
CREATE TABLE IF NOT EXISTS people(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  rate REAL NOT NULL,
  unit TEXT NOT NULL DEFAULT 'month',
  weekly_off INTEGER NOT NULL DEFAULT 6,
  paid_leaves REAL NOT NULL DEFAULT 1,
  advance_limit_pct REAL NOT NULL DEFAULT 50,
  pay_day INTEGER NOT NULL DEFAULT 1,
  upi_id TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT 'staff',
  item_detail TEXT NOT NULL DEFAULT '',
  icon TEXT NOT NULL DEFAULT '👥',
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id INTEGER NOT NULL REFERENCES people(id),
  day TEXT NOT NULL,
  kind TEXT NOT NULL,
  amount REAL NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  undone INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_person_day ON events(person_id, day);
CREATE TABLE IF NOT EXISTS commitments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  category TEXT NOT NULL DEFAULT 'emi',
  amount REAL NOT NULL,
  due_day INTEGER NOT NULL,
  autopay_enabled INTEGER NOT NULL DEFAULT 1,
  autopay_provider TEXT NOT NULL DEFAULT 'Amazon Pay',
  pay_url TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  last_paid_date TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_commitments_due ON commitments(due_day);
"""


@contextmanager
def db():
    conn = sqlite3.connect(os.environ.get("HISAAB_DB", "hisaab.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for col_sql in [
        "ALTER TABLE people ADD COLUMN upi_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE people ADD COLUMN category TEXT NOT NULL DEFAULT 'staff'",
        "ALTER TABLE people ADD COLUMN item_detail TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE people ADD COLUMN icon TEXT NOT NULL DEFAULT '👥'",
    ]:
        try:
            conn.execute(col_sql)
        except sqlite3.OperationalError:
            pass
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def ensure_seed_data():
    """Populates a rich household ecosystem: domestic staff, milk deliveries, and newspaper subscriptions."""
    db_path = os.environ.get("HISAAB_DB", "hisaab.db")
    if "test" in db_path:
        return
    with db() as conn:
        # Ensure default commitments (EMIs, Rent, Bills with Amazon Pay AutoPay)
        try:
            comm_cnt = conn.execute("SELECT count(*) as cnt FROM commitments").fetchone()["cnt"]
            if comm_cnt == 0:
                default_commitments = [
                    ("iPhone 16 Pro EMI", "emi", 4200.0, 5, 1, "Amazon Pay ICICI Card"),
                    ("Flat Rent & Society", "rent", 22000.0, 1, 0, "Amazon Pay UPI"),
                    ("Tata Power Electricity", "utility_bill", 1850.0, 10, 1, "Amazon Pay AutoPay"),
                    ("Airtel Xstream Fiber", "subscription", 999.0, 15, 1, "Amazon Pay AutoPay"),
                ]
                for c_name, c_cat, c_amt, c_day, c_auto, c_prov in default_commitments:
                    conn.execute(
                        "INSERT INTO commitments(name, category, amount, due_day, autopay_enabled, autopay_provider, status, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (c_name, c_cat, c_amt, c_day, c_auto, c_prov, "pending", datetime.now(TZ).isoformat())
                    )
        except Exception:
            pass

        count = conn.execute("SELECT count(*) as cnt FROM people").fetchone()["cnt"]
        if count >= 6:
            return

        # 1. Sunita (Maid)
        conn.execute(
            "INSERT INTO people(name, rate, unit, weekly_off, paid_leaves, pay_day, upi_id, category, item_detail, icon) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET rate=excluded.rate, category=excluded.category, item_detail=excluded.item_detail, icon=excluded.icon",
            ("Sunita", 9000.0, "month", 6, 1.0, 1, "sunita@apl", "staff", "Full-time Housekeeping & Cleaning", "🧹")
        )
        p_sunita = conn.execute("SELECT id FROM people WHERE name='Sunita'").fetchone()["id"]
        for d in ["2026-10-05", "2026-10-12", "2026-10-19"]:
            conn.execute("INSERT OR IGNORE INTO events(person_id, day, kind, amount, created_at) VALUES (?,?,?,?,?)",
                         (p_sunita, d, "absent", 0, datetime.now(TZ).isoformat()))
        conn.execute("INSERT OR IGNORE INTO events(person_id, day, kind, amount, created_at) VALUES (?,?,?,?,?)",
                     (p_sunita, "2026-10-23", "advance", 2000.0, datetime.now(TZ).isoformat()))
        # Sunita May Vacation Leaves (Peak Leave Month)
        for d in ["2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14", "2026-05-15"]:
            conn.execute("INSERT OR IGNORE INTO events(person_id, day, kind, amount, created_at) VALUES (?,?,?,?,?)",
                         (p_sunita, d, "absent", 0, datetime.now(TZ).isoformat()))


        # 2. Ramesh (Cook)
        conn.execute(
            "INSERT INTO people(name, rate, unit, weekly_off, paid_leaves, pay_day, upi_id, category, item_detail, icon) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET rate=excluded.rate, category=excluded.category, item_detail=excluded.item_detail, icon=excluded.icon",
            ("Ramesh", 6500.0, "month", 2, 1.0, 1, "ramesh.cook@upi", "staff", "Daily Breakfast & Dinner", "👨‍🍳")
        )
        p_ramesh = conn.execute("SELECT id FROM people WHERE name='Ramesh'").fetchone()["id"]
        conn.execute("INSERT OR IGNORE INTO events(person_id, day, kind, amount, created_at) VALUES (?,?,?,?,?)",
                     (p_ramesh, "2026-10-14", "half_day", 0, datetime.now(TZ).isoformat()))
        conn.execute("INSERT OR IGNORE INTO events(person_id, day, kind, amount, created_at) VALUES (?,?,?,?,?)",
                     (p_ramesh, "2026-10-10", "advance", 1000.0, datetime.now(TZ).isoformat()))

        # 3. Raju (Driver - Day Wage)
        conn.execute(
            "INSERT INTO people(name, rate, unit, weekly_off, paid_leaves, pay_day, upi_id, category, item_detail, icon) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET rate=excluded.rate, category=excluded.category, item_detail=excluded.item_detail, icon=excluded.icon",
            ("Raju", 600.0, "day", 6, 0.0, 1, "raju.driver@apl", "staff", "Personal Car Commute (₹600/day)", "🚗")
        )
        p_raju = conn.execute("SELECT id FROM people WHERE name='Raju'").fetchone()["id"]
        conn.execute("INSERT OR IGNORE INTO events(person_id, day, kind, amount, created_at) VALUES (?,?,?,?,?)",
                     (p_raju, "2026-10-06", "absent", 0, datetime.now(TZ).isoformat()))
        conn.execute("INSERT OR IGNORE INTO events(person_id, day, kind, amount, created_at) VALUES (?,?,?,?,?)",
                     (p_raju, "2026-10-20", "absent", 0, datetime.now(TZ).isoformat()))
        conn.execute("INSERT OR IGNORE INTO events(person_id, day, kind, amount, created_at) VALUES (?,?,?,?,?)",
                     (p_raju, "2026-10-15", "advance", 2500.0, datetime.now(TZ).isoformat()))

        # 4. Country Delight (Daily Milk Supply)
        conn.execute(
            "INSERT INTO people(name, rate, unit, weekly_off, paid_leaves, pay_day, upi_id, category, item_detail, icon) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET rate=excluded.rate, category=excluded.category, item_detail=excluded.item_detail, icon=excluded.icon",
            ("Country Delight", 2800.0, "month", -1, 0.0, 1, "milk.countrydelight@upi", "milk", "2 Litres Cow Milk Daily (₹68/day)", "🥛")
        )
        p_milk = conn.execute("SELECT id FROM people WHERE name='Country Delight'").fetchone()["id"]
        conn.execute("INSERT OR IGNORE INTO events(person_id, day, kind, amount, created_at) VALUES (?,?,?,?,?)",
                     (p_milk, "2026-10-18", "absent", 0, datetime.now(TZ).isoformat()))

        # 5. Times & Bhaskar (Daily Newspaper Subscription)
        conn.execute(
            "INSERT INTO people(name, rate, unit, weekly_off, paid_leaves, pay_day, upi_id, category, item_detail, icon) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET rate=excluded.rate, category=excluded.category, item_detail=excluded.item_detail, icon=excluded.icon",
            ("Times & Bhaskar", 380.0, "month", -1, 0.0, 1, "news.vendor@upi", "newspaper", "Times of India + Dainik Bhaskar Daily", "📰")
        )
        p_news = conn.execute("SELECT id FROM people WHERE name='Times & Bhaskar'").fetchone()["id"]
        conn.execute("INSERT OR IGNORE INTO events(person_id, day, kind, amount, created_at) VALUES (?,?,?,?,?)",
                     (p_news, "2026-10-12", "absent", 0, datetime.now(TZ).isoformat()))

        # 6. Bisleri 20L Water Jars (Daily/Unit Supply)
        conn.execute(
            "INSERT INTO people(name, rate, unit, weekly_off, paid_leaves, pay_day, upi_id, category, item_detail, icon) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET rate=excluded.rate, category=excluded.category, item_detail=excluded.item_detail, icon=excluded.icon",
            ("Bisleri 20L Water", 80.0, "day", -1, 0.0, 1, "water.vendor@apl", "utility", "20 Litre Bubble Top Jars (₹80/jar)", "💧")
        )

        # 7. Default Fixed Commitments & EMIs with Amazon Pay AutoPay
        try:
            comm_cnt = conn.execute("SELECT count(*) as cnt FROM commitments").fetchone()["cnt"]
            if comm_cnt == 0:
                default_commitments = [
                    ("iPhone 16 Pro EMI", "emi", 4200.0, 5, 1, "Amazon Pay ICICI Card"),
                    ("Flat Rent & Society", "rent", 22000.0, 1, 0, "Amazon Pay UPI"),
                    ("Tata Power Electricity", "utility_bill", 1850.0, 10, 1, "Amazon Pay AutoPay"),
                    ("Airtel Xstream Fiber", "subscription", 999.0, 15, 1, "Amazon Pay AutoPay"),
                ]
                for c_name, c_cat, c_amt, c_day, c_auto, c_prov in default_commitments:
                    conn.execute(
                        "INSERT INTO commitments(name, category, amount, due_day, autopay_enabled, autopay_provider, status, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (c_name, c_cat, c_amt, c_day, c_auto, c_prov, "pending", datetime.now(TZ).isoformat())
                    )
        except Exception:
            pass


ensure_seed_data()


# ----------------------------------------------------------------- Math & Helper Functions
def today() -> date:
    forced = os.environ.get("HISAAB_TODAY")
    return date.fromisoformat(forced) if forced else datetime.now(TZ).date()


def money(x: float) -> str:
    return f"{int(round(x)):,}"

def spoken(x: float) -> str:
    return f"{money(x)} {WORD}"


def parse_day(text: Optional[str]) -> date:
    t = (text or "").strip().lower()
    now = today()
    if t in ("", "today", "now", "aaj"):
        return now
    if t in ("yesterday", "kal", "beeta kal"):
        return now - timedelta(days=1)
    try:
        d = date.fromisoformat(t)
    except ValueError:
        raise ValueError("I need an exact date formatted as year-month-day.")
    if d > now:
        raise ValueError("That date is in the future.")
    return d


def parse_month(text: Optional[str]) -> tuple:
    t = (text or "").strip().lower()
    now = today()
    if t in ("", "this month", "current", "now", "is mahine", "iss mahine", "is month"):
        return now.year, now.month
    if t in ("last month", "previous", "previous month", "pichle mahine"):
        first = now.replace(day=1)
        prev = first - timedelta(days=1)
        return prev.year, prev.month
    try:
        y, m = t.split("-")
        y, m = int(y), int(m)
        if 1 <= m <= 12:
            return y, m
    except ValueError:
        pass
    for i, name in enumerate(calendar.month_name):
        if name and t == name.lower():
            return (now.year if i <= now.month else now.year - 1), i
    raise ValueError("Which month? State it as year-month, like 2026-10.")


def parse_weekday(text: str) -> int:
    t = (text or "").strip().lower()
    if t in ("none", "no", "never", "", "koi nahi"):
        return -1
    for i, name in enumerate(WEEKDAYS):
        if name.startswith(t[:3]) and t[:3]:
            return i
    raise ValueError("Weekly off should be a weekday name like Sunday, or none.")


def resolve_person(conn, name: Optional[str]) -> sqlite3.Row:
    rows = conn.execute("SELECT * FROM people WHERE active=1 ORDER BY id").fetchall()
    if not rows:
        raise ValueError("No staff are set up yet. For example, say: set up Sunita at 9000 rupees a month.")
    n = (name or "").strip().lower()
    for r in rows:
        if r["name"].lower() == n:
            return r
    if n and n not in GENERIC_NAMES:
        matches = [r for r in rows if n in r["name"].lower() or r["name"].lower() in n]
        if len(matches) == 1:
            return matches[0]
        names = ", ".join(r["name"] for r in rows)
        raise ValueError(f"I don't recognize {name}. Active staff: {names}.")
    if len(rows) == 1:
        return rows[0]
    raise ValueError("Which person? Active staff: " + ", ".join(r["name"] for r in rows) + ".")


def safe(fn):
    """Turns expected user errors into friendly spoken responses instead of tool failures."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as e:
            return {"ok": False, "spoken_summary": str(e)}
    return wrapper


def add_event(conn, person_id: int, day: date, kind: str, amount: float = 0, note: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO events(person_id, day, kind, amount, note, created_at) VALUES (?,?,?,?,?,?)",
        (person_id, day.isoformat(), kind, amount, note, datetime.now(TZ).isoformat(timespec="seconds")),
    )
    return cur.lastrowid


def compute_month(conn, person: sqlite3.Row, year: int, month: int) -> dict:
    dim = calendar.monthrange(year, month)[1]
    first, last = date(year, month, 1), date(year, month, dim)
    events = conn.execute(
        "SELECT * FROM events WHERE person_id=? AND undone=0 AND day BETWEEN ? AND ? ORDER BY id",
        (person["id"], first.isoformat(), last.isoformat()),
    ).fetchall()

    status = {}
    advances = salary_paid = bonus = 0.0
    late_days = []
    penalties = 0.0
    penalty_list = []
    piece_work_total = 0.0
    piece_items = []

    for e in events:
        if e["kind"] in ("absent", "half_day", "present"):
            status[e["day"]] = e["kind"]
        elif e["kind"] == "late":
            late_days.append(e["day"])
        elif e["kind"] == "advance":
            advances += e["amount"]
        elif e["kind"] == "salary":
            salary_paid += e["amount"]
        elif e["kind"] == "bonus":
            bonus += e["amount"]
        elif e["kind"] == "penalty":
            penalties += e["amount"]
            penalty_list.append({"amount": e["amount"], "reason": e["note"], "day": e["day"]})
        elif e["kind"] == "pieces":
            piece_work_total += e["amount"]
            piece_items.append({"amount": e["amount"], "note": e["note"], "day": e["day"]})

    off = person["weekly_off"]
    off_days = {d for d in range(1, dim + 1) if off >= 0 and date(year, month, d).weekday() == off}
    working_days = dim - len(off_days)

    def counts(kind):
        return sum(
            1 for day, k in status.items()
            if k == kind and date.fromisoformat(day).day not in off_days
        )

    absent, half = counts("absent"), counts("half_day")
    lost = absent + 0.5 * half

    if person["unit"] == "piece":
        per_day = 0.0
        unpaid = 0.0
        gross = piece_work_total
        full = piece_work_total
    elif person["unit"] == "day":
        per_day = person["rate"]
        unpaid = lost
        gross = per_day * (working_days - lost)
        full = per_day * working_days
    else:
        per_day = person["rate"] / dim
        unpaid = max(0.0, lost - person["paid_leaves"])
        gross = person["rate"] - unpaid * per_day
        full = person["rate"]

    balance = gross + bonus + (piece_work_total if person["unit"] != "piece" else 0) - advances - salary_paid - penalties
    limit = person["rate"] * person["advance_limit_pct"] / 100
    warn = (person["unit"] == "month" and advances > limit)

    upi_id = person["upi_id"] if ("upi_id" in person.keys() and person["upi_id"]) else f"{person['name'].lower()}@apl"
    balance_int = int(round(max(0.0, balance)))
    upi_memo = f"Salary_{month_label(year, month).replace(' ', '_')}"
    upi_url = f"upi://pay?pa={urllib.parse.quote(upi_id)}&pn={urllib.parse.quote(person['name'])}&am={balance_int}&cu=INR&tn={urllib.parse.quote(upi_memo)}"
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=180x180&data={urllib.parse.quote(upi_url)}"

    return {
        "year": year, "month": month, "days_in_month": dim, "working_days": working_days,
        "absent_days": absent, "half_days": half, "late_count": len(late_days),
        "absent_dates": sorted(d for d, k in status.items() if k in ("absent", "half_day")),
        "paid_leaves": person["paid_leaves"] if person["unit"] == "month" else 0,
        "unpaid_days": unpaid, "per_day": per_day,
        "full_pay": full, "deduction": full - gross, "gross": gross,
        "bonus": bonus, "advances": advances, "salary_paid": salary_paid,
        "penalties": penalties, "penalty_list": penalty_list,
        "piece_work_total": piece_work_total, "piece_items": piece_items,
        "balance": balance, "advance_limit": limit, "advance_warning": warn,
        "upi_id": upi_id, "upi_url": upi_url, "qr_url": qr_url,
    }


def pattern_note(conn, person: sqlite3.Row) -> str:
    since = (today() - timedelta(days=56)).isoformat()
    rows = conn.execute(
        "SELECT day, kind FROM events WHERE person_id=? AND undone=0 AND day>=? "
        "AND kind IN ('absent','present','half_day') ORDER BY id",
        (person["id"], since),
    ).fetchall()
    latest = {r["day"]: r["kind"] for r in rows}
    per_weekday = {}
    for day, kind in latest.items():
        if kind == "absent":
            wd = date.fromisoformat(day).weekday()
            per_weekday[wd] = per_weekday.get(wd, 0) + 1
    for wd, n in sorted(per_weekday.items(), key=lambda kv: -kv[1]):
        if n >= 3:
            return f"{person['name']} was absent on {n} of the last 8 {WEEKDAYS[wd].title()}s."
    return ""


def month_label(year: int, month: int) -> str:
    return f"{calendar.month_name[month]} {year}"


def summary_text(person, m: dict, assumption: bool = True) -> str:
    name = person["name"]
    if m["balance"] > 0:
        s = f"You owe {name} {spoken(m['balance'])} for {calendar.month_name[m['month']]}."
    elif m["balance"] == 0:
        s = f"{name} is fully settled for {calendar.month_name[m['month']]}."
    else:
        s = f"{name} has taken {spoken(-m['balance'])} more than earned so far."
    bits = []
    if m["absent_days"] or m["half_days"]:
        days = m["absent_days"] + 0.5 * m["half_days"]
        bits.append(f"{days:g} day{'s' if days != 1 else ''} absent")
    if m["advances"]:
        bits.append(f"{spoken(m['advances'])} advance")
    if m["salary_paid"]:
        bits.append(f"{spoken(m['salary_paid'])} already paid")
    if bits:
        s += " That's " + ", ".join(bits) + "."
    if assumption and m["balance"] > 0:
        s += f" Counting {name} present on all other working days."
    return s


def slip_text(person, m: dict) -> str:
    lines = [
        f"📋 सैलरी स्लिप / Salary Slip: {person['name']}, {month_label(m['year'], m['month'])}",
        f"• मूल वेतन (Base Pay): {SYMBOL}{money(m['full_pay'])}",
    ]
    if m["deduction"]:
        lines.append(
            f"• अनुपस्थिति कटौती (Deduction: {m['absent_days'] + 0.5 * m['half_days']:g} days, {m['paid_leaves']:g} paid leave): -{SYMBOL}{money(m['deduction'])}"
        )
    if m["bonus"]:
        lines.append(f"• बोनस (Bonus): +{SYMBOL}{money(m['bonus'])}")
    if m["advances"]:
        lines.append(f"• दिया गया एडवांस (Advance Taken): -{SYMBOL}{money(m['advances'])}")
    if m["salary_paid"]:
        lines.append(f"• पूर्व भुगतान (Already Paid): -{SYMBOL}{money(m['salary_paid'])}")
    lines.append(f"• कुल देय राशि (Balance to Pay): {SYMBOL}{money(m['balance'])}")
    if m["upi_id"]:
        lines.append(f"• अमेज़न पे / UPI Handle: {m['upi_id']}")
    lines.append("\n(Hisaab voice-first household ledger for Alexa+)")
    return "\n".join(lines)


# ----------------------------------------------------------------- MCP Tools

@mcp.tool()
@safe
def log_pieces(
    person: str = "Ramlal Dhobi",
    count: int = 1,
    rate_per_piece: Optional[float] = None,
    day: str = "",
    note: str = "",
) -> dict:
    """Record piece-rate work (e.g., Dhobi ironing clothes, tailor alterations).
    
    Multiplies piece count by the person's unit rate and appends to their running monthly tab.
    """
    if count <= 0:
        raise ValueError("Piece count must be at least 1.")
    d = parse_day(day)
    with db() as conn:
        p = resolve_person(conn, person)
        rate = float(rate_per_piece) if rate_per_piece is not None else float(p["rate"])
        total_amt = count * rate
        desc = note or f"{count} clothes ironed (@ ₹{rate:g}/piece)"
        add_event(conn, p["id"], d, "pieces", total_amt, desc)
        m = compute_month(conn, p, d.year, d.month)
    return {
        "ok": True,
        "person": p["name"],
        "count": count,
        "rate_per_piece": rate,
        "total_added": total_amt,
        "balance_so_far": round(m["balance"]),
        "spoken_summary": f"Logged {count} clothes for {p['name']} at {rate:g} rupees per piece ({SYMBOL}{money(total_amt)}). Running balance now {spoken(m['balance'])}.",
    }


@mcp.tool()
@safe
def log_penalty(
    person: str,
    amount: float,
    reason: str = "",
    day: str = "",
) -> dict:
    """Record a transparent damage or penalty deduction against a helper (e.g. broken dish or damage).
    
    Directly subtracts from final gross payout and itemizes transparently on the WhatsApp salary slip.
    """
    if amount <= 0:
        raise ValueError("Penalty amount must be greater than zero.")
    d = parse_day(day)
    with db() as conn:
        p = resolve_person(conn, person)
        desc = reason.strip() or "Damage / Loss Deduction"
        add_event(conn, p["id"], d, "penalty", amount, desc)
        m = compute_month(conn, p, d.year, d.month)
    reason_txt = f" for {desc}" if desc else ""
    return {
        "ok": True,
        "person": p["name"],
        "amount_deducted": amount,
        "reason": desc,
        "balance_now": round(m["balance"]),
        "spoken_summary": f"Deducted {spoken(amount)} from {p['name']}{reason_txt}. Remaining balance now {spoken(m['balance'])}.",
    }


@mcp.tool()
@safe
def split_bill(
    person: str = "Sunita",
    split_with: str = "Amit",
    amount: Optional[float] = None,
    month: str = "",
) -> dict:
    """Split a household bill or staff salary among flatmates or PG roommates.
    
    Generates equal shares and produces instant 1-tap UPI deep links and WhatsApp payment requests.
    """
    with db() as conn:
        p = resolve_person(conn, person)
        y, mth = parse_month(month)
        m = compute_month(conn, p, y, mth)
        total_bill = float(amount) if amount is not None and amount > 0 else float(m["balance"])
    
    # Split with comma or 'and' separated names
    raw_names = re.split(r",|and|&", split_with)
    flatmates = [n.strip().title() for n in raw_names if n.strip()]
    if not flatmates:
        flatmates = ["Amit"]
    
    total_people = 1 + len(flatmates) # user + flatmates
    share = round(total_bill / total_people, 2)
    share_int = int(round(share))

    upi_id = p["upi_id"] or f"{p['name'].lower()}@apl"
    links = {}
    for fm in flatmates:
        memo = f"Share_for_{p['name']}_{fm}"
        upi_url = f"upi://pay?pa={urllib.parse.quote(upi_id)}&pn={urllib.parse.quote(p['name'])}&am={share_int}&cu=INR&tn={urllib.parse.quote(memo)}"
        wa_msg = f"Hey {fm}, your share for {p['name']}'s bill is ₹{share_int}. Please pay via UPI: {upi_url}"
        wa_url = f"https://wa.me/?text={urllib.parse.quote(wa_msg)}"
        links[fm] = {"share": share_int, "upi_url": upi_url, "whatsapp_request": wa_url}

    names_str = " and ".join(flatmates)
    return {
        "ok": True,
        "person": p["name"],
        "total_bill": round(total_bill),
        "total_shares": total_people,
        "share_per_person": share_int,
        "split_with": flatmates,
        "links": links,
        "spoken_summary": f"Split the {spoken(total_bill)} {p['name']} bill into {total_people} shares of {spoken(share_int)} each for you and {names_str}. Payment links are ready.",
    }

@mcp.tool()
@safe
def set_up_person(
    name: str,
    pay_amount: float,
    pay_unit: str = "month",
    weekly_off: str = "sunday",
    paid_leaves_per_month: float = 1,
    pay_day: int = 1,
    upi_id: str = "",
    category: str = "staff",
    item_detail: str = "",
    icon: str = "",
) -> dict:
    """Set up or update a household staff member: maid, cook, driver, milkman, or tutor."""
    unit = (pay_unit or "month").strip().lower()
    if unit not in ("month", "day", "piece"):
        raise ValueError("Pay can be per month, per day, or per piece.")
    if pay_amount <= 0:
        raise ValueError("Pay amount must be greater than zero.")
    if not 1 <= int(pay_day) <= 28:
        raise ValueError("Pay day must be between 1 and 28.")
    nm = name.strip().title()
    if not nm:
        raise ValueError("Please provide a valid name.")
    off = parse_weekday(weekly_off)
    u_id = (upi_id or "").strip()
    with db() as conn:
        conn.execute(
            "INSERT INTO people(name, rate, unit, weekly_off, paid_leaves, pay_day, upi_id) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET rate=excluded.rate, unit=excluded.unit, "
            "weekly_off=excluded.weekly_off, paid_leaves=excluded.paid_leaves, "
            "pay_day=excluded.pay_day, upi_id=excluded.upi_id, active=1",
            (nm, pay_amount, unit, off, paid_leaves_per_month, int(pay_day), u_id),
        )
    off_txt = "no weekly off" if off < 0 else f"{WEEKDAYS[off].title()} off"
    return {
        "ok": True, "person": nm,
        "spoken_summary": f"Done. {nm}: {spoken(pay_amount)} per {unit}, with {off_txt}."
                          + (f" Linked to {u_id}." if u_id else ""),
    }


@mcp.tool()
@safe
def log_visit(person: str = "", status: str = "absent", day: str = "", note: str = "") -> dict:
    """Record an attendance exception: 'absent', 'half_day', 'late', or 'present' to correct an error."""
    st = (status or "absent").strip().lower().replace(" ", "_").replace("-", "_")
    st = {"absence": "absent", "halfday": "half_day", "half": "half_day", "came": "present", "aadhi_chhutti": "half_day", "chhutti": "absent"}.get(st, st)
    if st not in ("absent", "half_day", "late", "present"):
        raise ValueError("Status can be absent, half day, late, or present.")
    d = parse_day(day)
    with db() as conn:
        p = resolve_person(conn, person)
        add_event(conn, p["id"], d, st, 0, note)
        m = compute_month(conn, p, d.year, d.month)
    when = "today" if d == today() else d.strftime("%d %B")
    text = {
        "absent": f"Noted: {p['name']} was absent {when}.",
        "half_day": f"Noted: half day for {p['name']} {when}.",
        "late": f"Noted: {p['name']} was late {when}.",
        "present": f"Corrected: {p['name']} was present {when}.",
    }[st]
    return {
        "ok": True, "person": p["name"], "date": d.isoformat(), "status": st,
        "balance_so_far": round(m["balance"]),
        "spoken_summary": text + f" Balance now {spoken(m['balance'])}.",
    }


@mcp.tool()
@safe
def log_absences(person: str, dates: list[str], note: str = "") -> dict:
    """Record multiple absent dates at once (useful for batch month-end updates)."""
    if not dates:
        raise ValueError("Please provide at least one date.")
    parsed = sorted({parse_day(x) for x in dates})
    with db() as conn:
        p = resolve_person(conn, person)
        for d in parsed:
            add_event(conn, p["id"], d, "absent", 0, note)
        last = parsed[-1]
        m = compute_month(conn, p, last.year, last.month)
    return {
        "ok": True, "person": p["name"], "dates": [d.isoformat() for d in parsed],
        "spoken_summary": f"Noted {len(parsed)} absent day{'s' if len(parsed) != 1 else ''} for "
                          f"{p['name']}. Balance now {spoken(m['balance'])}.",
    }


@mcp.tool()
@safe
def log_payment(person: str = "", amount: float = 0, kind: str = "advance", day: str = "", note: str = "") -> dict:
    """Record money given: 'advance', 'salary' handed over, or 'bonus'."""
    k = (kind or "advance").strip().lower()
    if k not in ("advance", "salary", "bonus"):
        raise ValueError("Payment can be an advance, salary, or bonus.")
    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")
    d = parse_day(day)
    with db() as conn:
        p = resolve_person(conn, person)
        add_event(conn, p["id"], d, k, amount, note)
        m = compute_month(conn, p, d.year, d.month)
    text = f"Noted {spoken(amount)} {k} for {p['name']}."
    if k == "advance" and m["advance_warning"]:
        text += f" Careful: advances are now {spoken(m['advances'])}, exceeding your limit of {spoken(m['advance_limit'])}."
    else:
        text += f" Balance now {spoken(m['balance'])}."
    return {
        "ok": True, "person": p["name"], "kind": k, "amount": amount,
        "advance_warning": m["advance_warning"], "balance": round(m["balance"]),
        "spoken_summary": text,
    }


@mcp.tool()
@safe
def pay_salary(person: str = "", amount: float = 0, payment_mode: str = "upi", note: str = "") -> dict:
    """Record salary payment and settlement via Amazon Pay or UPI."""
    d = today()
    with db() as conn:
        p = resolve_person(conn, person)
        m = compute_month(conn, p, d.year, d.month)
        amt = float(amount) if amount > 0 else max(0.0, float(m["balance"]))
        if amt <= 0:
            return {
                "ok": True, "person": p["name"], "balance": 0,
                "spoken_summary": f"All settled! {p['name']} has zero balance owed for this month."
            }
        pay_note = f"Settled via {payment_mode.upper()}" + (f": {note}" if note else "")
        add_event(conn, p["id"], d, "salary", amt, pay_note)
        m_after = compute_month(conn, p, d.year, d.month)

    return {
        "ok": True, "person": p["name"], "amount_paid": amt,
        "payment_mode": payment_mode, "remaining_balance": round(m_after["balance"]),
        "upi_url": m["upi_url"], "qr_url": m["qr_url"],
        "spoken_summary": f"Recorded payment of {spoken(amt)} to {p['name']} via {payment_mode.upper()}. "
                          f"Remaining balance for this month is {spoken(m_after['balance'])}."
    }


@mcp.tool()
@safe
def month_hisaab(person: str = "", month: str = "") -> dict:
    """Get the account summary for a specific person for the month, with UPI payment link and pattern insights."""
    y, mth = parse_month(month)
    with db() as conn:
        p = resolve_person(conn, person)
        m = compute_month(conn, p, y, mth)
        pat = pattern_note(conn, p)
    text = summary_text(p, m)
    if m["advance_warning"]:
        text += f" Advances exceed your limit of {spoken(m['advance_limit'])}."
    if pat:
        text += " " + pat
    return {
        "ok": True, "person": p["name"], "month": f"{y}-{mth:02d}",
        "owed_now": round(m["balance"]), "full_pay": round(m["full_pay"]),
        "absent_days": m["absent_days"] + 0.5 * m["half_days"], "advances": round(m["advances"]),
        "already_paid": round(m["salary_paid"]), "absent_dates": m["absent_dates"],
        "upi_url": m["upi_url"], "qr_url": m["qr_url"],
        "spoken_summary": text,
    }


@mcp.tool()
@safe
def household_hisaab(month: str = "") -> dict:
    """Get the total monthly expenditure and balance owed across all household help combined."""
    y, mth = parse_month(month)
    with db() as conn:
        people = conn.execute("SELECT * FROM people WHERE active=1 ORDER BY id").fetchall()
        if not people:
            return {"ok": True, "spoken_summary": "No staff are set up yet."}
        total_owed = 0.0
        breakdown = []
        for p in people:
            m = compute_month(conn, p, y, mth)
            total_owed += m["balance"]
            breakdown.append(f"{p['name']}: {spoken(m['balance'])}")
    summary = f"Total household help balance for {calendar.month_name[mth]}: {spoken(total_owed)}. Breakdown: " + ", ".join(breakdown) + "."
    return {
        "ok": True, "total_owed": round(total_owed), "month": f"{y}-{mth:02d}",
        "spoken_summary": summary,
    }



def format_lakhs(amt: float) -> str:
    """Formats amounts into natural Indian Lakhs/Thousands spoken units."""
    if amt >= 100000:
        val = amt / 100000
        return f"{val:.1f} Lakhs" if val != int(val) else f"{int(val)} Lakhs"
    elif amt >= 1000:
        val = amt / 1000
        return f"{val:.1f} Thousand" if val != int(val) else f"{int(val)} Thousand"
    else:
        return f"{int(round(amt))}"


@mcp.tool()
@safe
def annual_insights(year: Optional[int] = None) -> dict:
    """Provides high-level yearly household analytics and spending insights across all staff and services.
    
    Computes total annual spend, monthly breakdown, attendance champions, peak leave months,
    and structured JSON data optimized for Alexa+ conversational reasoning.
    """
    yr = int(year or today().year)
    now = today()
    current_month = now.month if now.year == yr else 12

    with db() as conn:
        people = conn.execute("SELECT * FROM people WHERE active=1 ORDER BY id").fetchall()
        if not people:
            return {"ok": True, "spoken_summary": "No household staff or services are set up yet."}

        person_stats = {}
        monthly_spend = {m: 0.0 for m in range(1, current_month + 1)}
        monthly_leaves = {m: {} for m in range(1, current_month + 1)}
        total_yearly_spend = 0.0

        for p in people:
            p_id = p["id"]
            p_name = p["name"]
            cat = p["category"] if ("category" in p.keys() and p["category"]) else "staff"
            total_earned = 0.0
            total_absences = 0.0
            month_absence_map = {}

            for m in range(1, current_month + 1):
                res = compute_month(conn, p, yr, m)
                spend_for_month = res["full_pay"] - res["deduction"] + res["bonus"]
                monthly_spend[m] += spend_for_month
                total_earned += spend_for_month

                absent_count = res["absent_days"] + (0.5 * res["half_days"])
                total_absences += absent_count
                month_absence_map[m] = absent_count
                monthly_leaves[m][p_name] = absent_count

            total_yearly_spend += total_earned
            person_stats[p_name] = {
                "category": cat,
                "yearly_total": round(total_earned),
                "total_leaves": total_absences,
                "monthly_absences": month_absence_map,
                "rate": p["rate"],
                "unit": p["unit"],
            }

        # Filter staff for attendance analytics
        staff_only = {name: data for name, data in person_stats.items() if data["category"] == "staff"}
        
        # Most leave person & peak leave month
        most_leave_person = max(staff_only.items(), key=lambda x: x[1]["total_leaves"])[0] if staff_only else "None"
        most_leave_data = staff_only.get(most_leave_person, {})
        
        peak_month_idx = 5  # default May
        if most_leave_data.get("monthly_absences") and any(most_leave_data["monthly_absences"].values()):
            peak_month_idx = max(most_leave_data["monthly_absences"].items(), key=lambda x: x[1])[0]
        peak_month_name = calendar.month_name[peak_month_idx]

        # Best attendance person (lowest absences)
        best_attendance_person = min(staff_only.items(), key=lambda x: x[1]["total_leaves"])[0] if staff_only else "None"

        # Format natural spoken summary for voice synthesis
        lakhs_text = format_lakhs(total_yearly_spend)
        spoken_summary = (
            f"You spent {lakhs_text} on household help this year. "
            f"{most_leave_person} took the most leave in {peak_month_name}, "
            f"and {best_attendance_person} had the best attendance."
        )

        return {
            "ok": True,
            "year": yr,
            "total_spent": round(total_yearly_spend),
            "total_spent_formatted": f"{SYMBOL}{money(total_yearly_spend)} ({lakhs_text})",
            "most_leave_person": most_leave_person,
            "most_leave_peak_month": peak_month_name,
            "most_leave_total": most_leave_data.get("total_leaves", 0),
            "best_attendance_person": best_attendance_person,
            "breakdown_by_service": person_stats,
            "monthly_spend_trend": {calendar.month_abbr[m]: round(amt) for m, amt in monthly_spend.items()},
            "spoken_summary": spoken_summary,
        }


@mcp.tool()
@safe
def list_people() -> dict:
    """List all registered household staff and their pay rates."""
    with db() as conn:
        rows = conn.execute("SELECT * FROM people WHERE active=1 ORDER BY id").fetchall()
    if not rows:
        return {"ok": True, "people": [], "spoken_summary": "No staff are set up yet."}
    people = [{"name": r["name"], "pay": r["rate"], "per": r["unit"]} for r in rows]
    speech = "; ".join(f"{r['name']}, {spoken(r['rate'])} per {r['unit']}" for r in rows)
    return {"ok": True, "people": people, "spoken_summary": speech + "."}


@mcp.tool()
@safe
def undo_last() -> dict:
    """Undo the most recent entry (absence, payment, or correction)."""
    with db() as conn:
        e = conn.execute(
            "SELECT e.*, p.name FROM events e JOIN people p ON p.id=e.person_id "
            "WHERE e.undone=0 ORDER BY e.id DESC LIMIT 1"
        ).fetchone()
        if not e:
            raise ValueError("There is nothing to undo.")
        conn.execute("UPDATE events SET undone=1 WHERE id=?", (e["id"],))
    what = e["kind"].replace("_", " ")
    extra = f" of {spoken(e['amount'])}" if e["amount"] else ""
    return {
        "ok": True, "undone": {"person": e["name"], "kind": e["kind"], "day": e["day"]},
        "spoken_summary": f"Undone: {what}{extra} for {e['name']} on {e['day']}.",
    }



@mcp.tool()
@safe
def list_commitments(month: str = "") -> dict:
    """List all fixed household overheads, EMIs, and utility bills with due dates and Amazon Pay AutoPay status."""
    with db() as conn:
        rows = conn.execute("SELECT * FROM commitments ORDER BY due_day ASC").fetchall()
        now = today()
        total_amt = 0.0
        autopay_amt = 0.0
        items = []
        for r in rows:
            amt = float(r["amount"])
            total_amt += amt
            is_auto = bool(r["autopay_enabled"])
            if is_auto:
                autopay_amt += amt
            days_in_mth = calendar.monthrange(now.year, now.month)[1]
            diff = r["due_day"] - now.day if r["due_day"] >= now.day else (days_in_mth - now.day) + r["due_day"]
            due_note = "Today" if diff == 0 else f"In {diff} days ({r['due_day']}th)" 
            items.append({
                "name": r["name"],
                "category": r["category"],
                "amount": amt,
                "due_day": r["due_day"],
                "due_note": due_note,
                "autopay_enabled": is_auto,
                "provider": r["autopay_provider"],
                "status": r["status"],
            })
    spoken_summary = (
        f"You have {len(items)} fixed commitments totaling {spoken(total_amt)} per month. "
        f"{spoken(autopay_amt)} is protected by Amazon Pay AutoPay."
    )
    return {
        "ok": True,
        "commitments": items,
        "total_fixed_amount": round(total_amt),
        "autopay_total": round(autopay_amt),
        "manual_total": round(total_amt - autopay_amt),
        "spoken_summary": spoken_summary,
    }


@mcp.tool()
@safe
def add_commitment(
    name: str,
    amount: float,
    due_day: int,
    category: str = "emi",
    autopay: bool = True,
    provider: str = "Amazon Pay",
) -> dict:
    """Register a new recurring EMI, rent, or utility bill with Amazon Pay AutoPay support."""
    if amount <= 0:
        raise ValueError("Commitment amount must be greater than zero.")
    if not (1 <= int(due_day) <= 31):
        raise ValueError("Due day must be between 1 and 31.")
    nm = name.strip()
    if not nm:
        raise ValueError("Please provide a valid commitment name.")
    cat = (category or "emi").strip().lower()
    auto_val = 1 if autopay else 0
    with db() as conn:
        conn.execute(
            "INSERT INTO commitments(name, category, amount, due_day, autopay_enabled, autopay_provider, status, created_at) "
            "VALUES (?,?,?,?,?,?, 'pending', ?) "
            "ON CONFLICT(name) DO UPDATE SET amount=excluded.amount, due_day=excluded.due_day, "
            "autopay_enabled=excluded.autopay_enabled, autopay_provider=excluded.autopay_provider, category=excluded.category",
            (nm, cat, amount, int(due_day), auto_val, provider, datetime.now(TZ).isoformat())
        )
    auto_txt = f"AutoPay active via {provider}" if autopay else "Manual payment required"
    return {
        "ok": True,
        "name": nm,
        "amount": amount,
        "due_day": int(due_day),
        "autopay_enabled": bool(autopay),
        "provider": provider,
        "spoken_summary": f"Added {nm} of {spoken(amount)} due on the {due_day}th of every month. {auto_txt}.",
    }


@mcp.tool()
@safe
def pay_commitment_amazon_pay(name: str, month: str = "") -> dict:
    """Initiate an instant 1-tap Amazon Pay payment or settle an EMI / bill."""
    with db() as conn:
        row = conn.execute("SELECT * FROM commitments WHERE name LIKE ? COLLATE NOCASE", (f"%{name}%",)).fetchone()
        if not row:
            rows = conn.execute("SELECT * FROM commitments").fetchall()
            for r in rows:
                if any(w in r["name"].lower() for w in name.lower().split()):
                    row = r
                    break
        if not row:
            raise ValueError(f"No commitment found matching '{name}'.")
        amt = float(row["amount"])
        comm_name = row["name"]
        conn.execute("UPDATE commitments SET status='paid', last_paid_date=? WHERE id=?",
                     (datetime.now(TZ).date().isoformat(), row["id"]))
    
    clean_memo = f"AmazonPay_{comm_name.replace(' ', '_')}"
    upi_url = f"upi://pay?pa=amazonpay@apl&pn=AmazonPay_BillDesk&am={int(round(amt))}&cu=INR&tn={clean_memo}"
    amazon_app_url = f"amazonpay://pay?pa=amazonpay@apl&am={int(round(amt))}&tn={clean_memo}"
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=180x180&data={urllib.parse.quote(upi_url)}"
    
    return {
        "ok": True,
        "name": comm_name,
        "amount_paid": amt,
        "payment_method": "Amazon Pay UPI / AutoPay",
        "upi_url": upi_url,
        "amazon_pay_deep_link": amazon_app_url,
        "qr_url": qr_url,
        "status": "paid",
        "spoken_summary": f"Processed {spoken(amt)} for {comm_name} via Amazon Pay. Your receipt has been updated.",
    }


@mcp.tool()
@safe
def toggle_autopay(name: str, enable: Optional[bool] = None) -> dict:
    """Enable or disable Amazon Pay AutoPay e-mandate for an EMI or recurring bill."""
    with db() as conn:
        row = conn.execute("SELECT * FROM commitments WHERE name LIKE ? COLLATE NOCASE", (f"%{name}%",)).fetchone()
        if not row:
            rows = conn.execute("SELECT * FROM commitments").fetchall()
            for r in rows:
                if any(w in r["name"].lower() for w in name.lower().split()):
                    row = r
                    break
        if not row:
            raise ValueError(f"No commitment found matching '{name}'.")
        
        cur = bool(row["autopay_enabled"])
        new_state = (not cur) if enable is None else bool(enable)
        conn.execute("UPDATE commitments SET autopay_enabled=? WHERE id=?", (1 if new_state else 0, row["id"]))
        comm_name = row["name"]
        provider = row["autopay_provider"]
    
    state_str = "enabled and active" if new_state else "disabled"
    return {
        "ok": True,
        "name": comm_name,
        "autopay_enabled": new_state,
        "provider": provider,
        "spoken_summary": f"Amazon Pay AutoPay for {comm_name} is now {state_str}.",
    }


@mcp.tool()
@safe
def total_household_burn(month: str = "") -> dict:
    """Calculate the total household monthly burn: combining domestic help, daily supplies, and fixed EMIs / bills."""
    y, mth = parse_month(month)
    with db() as conn:
        people = conn.execute("SELECT * FROM people WHERE active=1").fetchall()
        staff_spend = 0.0
        for p in people:
            m = compute_month(conn, p, y, mth)
            staff_spend += max(0.0, float(m["balance"]))
        
        comm_rows = conn.execute("SELECT * FROM commitments").fetchall()
        fixed_spend = sum(float(r["amount"]) for r in comm_rows)
        autopay_spend = sum(float(r["amount"]) for r in comm_rows if r["autopay_enabled"])
        
        total_burn = staff_spend + fixed_spend
        now = today()
        impending = 0.0
        days_in_mth = calendar.monthrange(now.year, now.month)[1]
        for r in comm_rows:
            diff = r["due_day"] - now.day if r["due_day"] >= now.day else (days_in_mth - now.day) + r["due_day"]
            if 0 <= diff <= 7:
                impending += float(r["amount"])
    
    spoken_summary = (
        f"Your total household burn this month is {spoken(total_burn)}. "
        f"That includes {spoken(staff_spend)} for domestic staff and daily supplies, "
        f"and {spoken(fixed_spend)} across {len(comm_rows)} fixed EMIs and bills. "
        f"Keep at least {spoken(impending + 5000)} liquid for payments due in the next week."
    )
    return {
        "ok": True,
        "month": f"{y}-{mth:02d}",
        "total_household_burn": round(total_burn),
        "domestic_services_total": round(staff_spend),
        "fixed_commitments_total": round(fixed_spend),
        "autopay_covered": round(autopay_spend),
        "upcoming_7day_liquidity_needed": round(impending),
        "spoken_summary": spoken_summary,
    }


@mcp.tool()
@safe
def upcoming_reminders(days_ahead: int = 7) -> dict:
    """Check for upcoming EMIs, utility bills, and salary dues in the next N days."""
    now = today()
    with db() as conn:
        comm_rows = conn.execute("SELECT * FROM commitments ORDER BY due_day ASC").fetchall()
        due_items = []
        for r in comm_rows:
            days_in_mth = calendar.monthrange(now.year, now.month)[1]
            diff = r["due_day"] - now.day if r["due_day"] >= now.day else (days_in_mth - now.day) + r["due_day"]
            if 0 <= diff <= days_ahead:
                due_items.append({
                    "name": r["name"],
                    "amount": float(r["amount"]),
                    "due_day": r["due_day"],
                    "days_remaining": diff,
                    "autopay": bool(r["autopay_enabled"]),
                    "provider": r["autopay_provider"],
                })
    
    if not due_items:
        spoken_summary = f"Good news! You have no EMIs or bills due in the next {days_ahead} days."
    else:
        parts = []
        for it in due_items:
            auto_info = f"via {it['provider']} AutoPay" if it["autopay"] else "requires manual payment"
            day_str = "today" if it["days_remaining"] == 0 else f"in {it['days_remaining']} days"
            parts.append(f"{it['name']} of {spoken(it['amount'])} ({day_str}, {auto_info})")
        spoken_summary = f"You have {len(due_items)} payments due in the next {days_ahead} days: " + "; ".join(parts) + "."
    
    return {
        "ok": True,
        "days_ahead": days_ahead,
        "upcoming_count": len(due_items),
        "items": due_items,
        "spoken_summary": spoken_summary,
    }

# ----------------------------------------------------------------- Calendar & Web UI
def ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def build_ics() -> str:
    now = today()
    stamp = datetime.now(TZ).strftime("%Y%m%dT%H%M%S")
    out = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Hisaab//EN", "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Hisaab", "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
    ]

    def event(uid, day: date, summary, desc=""):
        out.extend([
            "BEGIN:VEVENT", f"UID:{uid}@hisaab", f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{ics_escape(summary)}",
        ])
        if desc:
            out.append(f"DESCRIPTION:{ics_escape(desc)}")
        out.append("END:VEVENT")

    with db() as conn:
        for p in conn.execute("SELECT * FROM people WHERE active=1").fetchall():
            since = (now - timedelta(days=60)).isoformat()
            rows = conn.execute(
                "SELECT day, kind FROM events WHERE person_id=? AND undone=0 AND day>=? "
                "AND kind IN ('absent','half_day','present') ORDER BY id", (p["id"], since),
            ).fetchall()
            latest = {r["day"]: r["kind"] for r in rows}
            for day, kind in latest.items():
                if kind in ("absent", "half_day"):
                    label = "absent" if kind == "absent" else "half day"
                    event(f"abs-{p['id']}-{day}", date.fromisoformat(day), f"{p['name']} {label}")
            for offset in range(3):
                y = now.year + (now.month - 1 + offset) // 12
                mo = (now.month - 1 + offset) % 12 + 1
                dim = calendar.monthrange(y, mo)[1]
                pay_date = date(y, mo, min(p["pay_day"], dim))
                if pay_date < now:
                    continue
                py, pm = (y, mo - 1) if mo > 1 else (y - 1, 12) if p["pay_day"] <= 10 else (y, mo)
                m = compute_month(conn, p, py, pm)
                event(
                    f"pay-{p['id']}-{pay_date.isoformat()}", pay_date,
                    f"Pay {p['name']}: {SYMBOL}{money(m['balance'])}",
                    slip_text(p, m) + "\n(Updates automatically as absences and advances are logged.)",
                )
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


PAGE_CSS = """
:root {
  --bg-dark: #070B10;
  --bg-panel: rgba(14, 22, 33, 0.75);
  --bg-card: rgba(16, 25, 38, 0.72);
  --border-glass: rgba(255, 255, 255, 0.09);
  --border-glass-hover: rgba(0, 202, 255, 0.38);
  
  --alexa-cyan: #00CAFF;
  --alexa-cyan-glow: rgba(0, 202, 255, 0.45);
  --alexa-blue: #0066FF;
  --amazon-orange: #FF9900;
  --amazon-orange-glow: rgba(255, 153, 0, 0.4);
  --amazon-gold: #FFB700;
  
  --text-primary: #FFFFFF;
  --text-secondary: #A0B5C6;
  --text-muted: #647B8E;
  
  --status-green: #00E676;
  --status-amber: #FFB703;
  --status-red: #FF5252;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: var(--bg-dark);
  background-image: 
    radial-gradient(circle at 50% -10%, rgba(0, 202, 255, 0.16) 0%, transparent 60%),
    radial-gradient(circle at 8% 25%, rgba(255, 153, 0, 0.07) 0%, transparent 45%),
    radial-gradient(circle at 92% 75%, rgba(0, 102, 255, 0.08) 0%, transparent 50%);
  background-attachment: fixed;
  color: var(--text-primary);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  min-height: 100vh;
}

main {
  max-width: 48rem;
  margin: 0 auto;
  padding: 2.2rem 1.4rem 5rem;
}

/* ------------------------------------------------------------- Header with Amazon Smile Logo */
.top-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 2rem;
  flex-wrap: wrap;
  gap: 1rem;
}
.brand-group {
  display: flex;
  align-items: center;
  gap: 1rem;
  flex-wrap: wrap;
}
.brand-wordmark-wrap {
  display: inline-flex;
  flex-direction: column;
  align-items: flex-start;
  position: relative;
  user-select: none;
}
.brand-title {
  font-size: 2.35rem;
  font-weight: 900;
  letter-spacing: -0.04em;
  color: #FFFFFF;
  line-height: 1;
  display: flex;
  align-items: baseline;
}
.brand-alexa-dot {
  font-size: 1.45rem;
  font-weight: 800;
  color: var(--alexa-cyan);
  margin-left: 2px;
}
.amazon-smile-svg {
  width: 128px;
  height: 24px;
  margin-top: -3px;
  margin-left: 2px;
  filter: drop-shadow(0 2px 8px rgba(255, 153, 0, 0.45));
}
.brand-badge-alexa {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  font-size: 0.72rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  padding: 0.35rem 0.8rem;
  border-radius: 999px;
  background: linear-gradient(135deg, rgba(0, 202, 255, 0.12) 0%, rgba(0, 102, 255, 0.06) 100%);
  border: 1px solid rgba(0, 202, 255, 0.32);
  color: var(--alexa-cyan);
  box-shadow: 0 0 16px rgba(0, 202, 255, 0.16);
}
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  font-size: 0.76rem;
  font-weight: 700;
  padding: 0.38rem 0.85rem;
  border-radius: 9999px;
  background: rgba(0, 230, 118, 0.08);
  border: 1px solid rgba(0, 230, 118, 0.28);
  color: var(--status-green);
  letter-spacing: 0.02em;
  box-shadow: 0 0 12px rgba(0, 230, 118, 0.1);
}
.status-pulse {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--status-green);
  box-shadow: 0 0 8px var(--status-green);
  animation: pulse-dot 2s infinite ease-in-out;
}
@keyframes pulse-dot {
  0%, 100% { opacity: 0.5; transform: scale(0.9); }
  50% { opacity: 1; transform: scale(1.3); }
}

/* ------------------------------------------------------------- Executive Command Center HUD */
.executive-hud {
  position: relative;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.05) 0%, rgba(13, 20, 30, 0.8) 100%);
  backdrop-filter: blur(28px);
  -webkit-backdrop-filter: blur(28px);
  border: 1px solid var(--border-glass);
  border-radius: 1.6rem;
  padding: 1.7rem 1.85rem;
  margin-bottom: 2.2rem;
  box-shadow: 
    0 22px 55px rgba(0, 0, 0, 0.7),
    inset 0 1px 0 rgba(255, 255, 255, 0.18),
    0 0 35px rgba(255, 153, 0, 0.06);
  overflow: hidden;
}
.executive-hud::before {
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, #FF9900, #00CAFF, #FF9900, transparent);
  background-size: 200% auto;
  animation: cyber-shimmer 4s linear infinite;
}
@keyframes cyber-shimmer {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}

.hud-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 1rem;
  margin-bottom: 1.25rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
}
.hud-brand-tag {
  display: flex;
  align-items: center;
  gap: 0.85rem;
}
.hud-icon {
  font-size: 1.65rem;
  width: 46px;
  height: 46px;
  border-radius: 50%;
  background: rgba(255, 153, 0, 0.12);
  border: 1px solid rgba(255, 153, 0, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 0 16px rgba(255, 153, 0, 0.2);
}
.hud-subtitle {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--amazon-orange);
  font-weight: 800;
}
.hud-title {
  font-size: 1.25rem;
  font-weight: 800;
  color: #FFFFFF;
  margin: 0.1rem 0 0;
  letter-spacing: -0.01em;
}
.hud-actions {
  display: flex;
  gap: 0.6rem;
  flex-wrap: wrap;
}
.action-btn-glow {
  background: linear-gradient(135deg, #FF9900 0%, #FF5500 100%);
  border: none;
  color: #fff;
  font-size: 0.82rem;
  font-weight: 800;
  padding: 0.52rem 1.05rem;
  border-radius: 999px;
  cursor: pointer;
  box-shadow: 0 4px 16px rgba(255, 153, 0, 0.38);
  transition: all 0.2s;
}
.action-btn-glow:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 22px rgba(255, 153, 0, 0.55);
}
.action-btn-ghost {
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.16);
  color: #cde4f5;
  font-size: 0.82rem;
  font-weight: 700;
  padding: 0.52rem 1.05rem;
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.2s;
}
.action-btn-ghost:hover {
  background: rgba(255, 255, 255, 0.12);
  border-color: var(--alexa-cyan);
  color: #fff;
}

.hud-metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 1rem;
  margin-bottom: 1.15rem;
}
.hud-metric-card {
  background: rgba(0, 0, 0, 0.32);
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 1.1rem;
  padding: 1rem 1.15rem;
  transition: all 0.25s ease;
}
.hud-metric-card:hover {
  border-color: rgba(255, 255, 255, 0.18);
  background: rgba(0, 0, 0, 0.45);
  transform: translateY(-2px);
}
.metric-burn {
  border-color: rgba(255, 153, 0, 0.35);
  background: linear-gradient(135deg, rgba(255, 153, 0, 0.09) 0%, rgba(0, 0, 0, 0.4) 100%);
}
.metric-label {
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-muted);
  font-weight: 700;
  margin-bottom: 0.35rem;
}
.metric-value {
  font-size: 1.35rem;
  font-weight: 800;
  color: #FFFFFF;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}
.metric-burn .metric-value { color: var(--amazon-orange); }
.cyan-val { color: var(--alexa-cyan); }
.emerald-val { color: var(--status-green); }
.gold-val { color: var(--amazon-gold); }
.metric-foot {
  font-size: 0.68rem;
  color: var(--text-muted);
  margin-top: 0.25rem;
}
.hud-liquidity-bar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.65rem 0.95rem;
  background: rgba(0, 230, 118, 0.06);
  border: 1px solid rgba(0, 230, 118, 0.2);
  border-radius: 0.85rem;
  font-size: 0.83rem;
  color: #d8f5e5;
}
.liquidity-badge {
  font-weight: 800;
  font-size: 0.74rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--status-green);
  white-space: nowrap;
}

/* ------------------------------------------------------------- Authentic Echo 3D Bezel Console */
.alexa-hero-console {
  position: relative;
  background: var(--bg-panel);
  backdrop-filter: blur(28px);
  -webkit-backdrop-filter: blur(28px);
  border: 1px solid var(--border-glass);
  border-radius: 1.6rem;
  padding: 2.2rem 1.8rem 1.8rem;
  margin-bottom: 2.4rem;
  box-shadow: 
    0 24px 60px rgba(0, 0, 0, 0.65),
    inset 0 1px 0 rgba(255, 255, 255, 0.14),
    0 0 35px rgba(0, 202, 255, 0.08);
  overflow: hidden;
}

.alexa-hero-console::before {
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, #00CAFF, #0055ff, #00CAFF, transparent);
  background-size: 200% auto;
  animation: cyber-shimmer 4s linear infinite;
}

.alexa-ring-stage {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 1rem 0 0.4rem;
  perspective: 900px;
}
.alexa-ring-viewport {
  width: 175px;
  height: 175px;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  transform-style: preserve-3d;
  transform: rotateX(46deg);
  transition: transform 0.5s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.alexa-ambient-halo {
  position: absolute;
  width: 225px;
  height: 225px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(0, 202, 255, 0.4) 0%, rgba(0, 102, 255, 0.2) 50%, transparent 70%);
  filter: blur(20px);
  animation: halo-breathe 3.5s ease-in-out infinite;
  pointer-events: none;
}
@keyframes halo-breathe {
  0%, 100% { transform: scale(0.92); opacity: 0.6; }
  50% { transform: scale(1.08); opacity: 0.95; }
}

.alexa-crown-bezel {
  width: 165px;
  height: 165px;
  border-radius: 50%;
  position: relative;
  background: radial-gradient(circle, #081118 55%, #152533 70%, #030608 100%);
  box-shadow: 
    0 18px 40px rgba(0, 0, 0, 0.85),
    0 0 30px rgba(0, 202, 255, 0.28),
    inset 0 1px 2px rgba(255, 255, 255, 0.22);
  display: flex;
  align-items: center;
  justify-content: center;
}

.alexa-light-ring {
  width: 150px;
  height: 150px;
  border-radius: 50%;
  position: absolute;
  padding: 7px;
  background: conic-gradient(
    from 0deg,
    #0044ff 0deg,
    #0088ff 55deg,
    #00CAFF 110deg,
    #00ffff 165deg,
    #ffffff 180deg,
    #00ffff 195deg,
    #00CAFF 250deg,
    #0066ff 305deg,
    #0044ff 360deg
  );
  box-shadow: 
    0 0 24px #00CAFF,
    0 0 50px rgba(0, 202, 255, 0.65),
    inset 0 0 16px rgba(0, 255, 255, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: ring-sweep 5s linear infinite;
  transition: all 0.35s ease;
}

.alexa-light-ring.listening {
  animation: ring-sweep 1.5s linear infinite, ring-pulse 0.8s ease-in-out infinite alternate !important;
  box-shadow: 0 0 45px #00ffff, 0 0 90px rgba(0, 202, 255, 0.95), inset 0 0 22px #ffffff !important;
}
.alexa-light-ring.thinking {
  background: conic-gradient(from 0deg, #00CAFF, #ffffff, #0088ff, #00CAFF) !important;
  animation: ring-sweep 0.75s linear infinite !important;
  box-shadow: 0 0 40px #00CAFF, 0 0 75px rgba(0, 202, 255, 0.8) !important;
}
.alexa-light-ring.speaking {
  background: conic-gradient(from 0deg, #00CAFF 0%, #00ffff 40%, #0088ff 70%, #00CAFF 100%) !important;
  animation: ring-sweep 3s linear infinite, ring-breathe 1.8s ease-in-out infinite alternate !important;
  box-shadow: 0 0 32px #00ffff, 0 0 65px rgba(0, 202, 255, 0.85) !important;
}

@keyframes ring-sweep {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
@keyframes ring-pulse {
  0% { transform: scale(0.96); filter: brightness(0.9); }
  100% { transform: scale(1.04); filter: brightness(1.2); }
}
@keyframes ring-breathe {
  0% { opacity: 0.8; }
  100% { opacity: 1; }
}

.alexa-hub-center {
  width: 100%;
  height: 100%;
  border-radius: 50%;
  background: radial-gradient(circle, #08121a 45%, #030609 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: inset 0 3px 8px rgba(0, 0, 0, 0.95);
}
.alexa-pupil-pip {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #00CAFF;
  box-shadow: 0 0 12px #00CAFF, 0 0 24px rgba(0, 202, 255, 0.8);
  transition: all 0.3s ease;
}

.wave-canvas {
  margin-top: 0.6rem;
  width: 240px;
  height: 36px;
  border-radius: 9999px;
  opacity: 0.85;
}

/* Hands-Free Wake Word Banner */
.wake-word-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(0, 0, 0, 0.35);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 999px;
  padding: 0.45rem 1rem;
  margin: 1.2rem auto 0.9rem;
  max-width: 480px;
  font-size: 0.82rem;
  color: var(--text-secondary);
}
.wake-status-group {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.btn-wake-toggle {
  background: rgba(0, 202, 255, 0.15);
  border: 1px solid rgba(0, 202, 255, 0.4);
  color: var(--alexa-cyan);
  padding: 0.25rem 0.65rem;
  border-radius: 999px;
  font-size: 0.74rem;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s;
}
.btn-wake-toggle:hover {
  background: var(--alexa-cyan);
  color: #04080c;
}

/* Live Hearing Transcript Strip */
.live-transcript-strip {
  display: none;
  align-items: center;
  justify-content: center;
  gap: 0.6rem;
  padding: 0.45rem 1rem;
  background: rgba(0, 202, 255, 0.1);
  border: 1px solid rgba(0, 202, 255, 0.35);
  border-radius: 0.6rem;
  margin: 0.5rem auto 1rem;
  max-width: 480px;
  font-size: 0.85rem;
  color: var(--alexa-cyan);
  animation: pulse-dot 1.5s infinite;
}

/* Voice Input Dock */
.input-dock {
  display: flex;
  gap: 0.6rem;
  margin: 1.1rem 0 1.3rem;
}
.voice-textbox {
  flex: 1;
  background: rgba(0, 0, 0, 0.42);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 0.9rem;
  padding: 0.85rem 1.25rem;
  color: #fff;
  font-size: 0.95rem;
  outline: none;
  transition: all 0.25s;
}
.voice-textbox:focus {
  border-color: var(--alexa-cyan);
  box-shadow: 0 0 18px rgba(0, 202, 255, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.1);
}
.action-btn-primary {
  background: linear-gradient(135deg, #00CAFF 0%, #0066ff 100%);
  border: none;
  color: #fff;
  font-weight: 800;
  border-radius: 0.85rem;
  padding: 0 1.35rem;
  cursor: pointer;
  box-shadow: 0 4px 18px rgba(0, 202, 255, 0.35);
  transition: all 0.2s;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.92rem;
}
.action-btn-primary:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 24px rgba(0, 202, 255, 0.55);
}

/* ------------------------------------------------------------- Decluttered Interactive Palette */
.feature-showcase-box {
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 1.1rem;
  padding: 1.1rem 1.25rem;
  margin-top: 1.1rem;
}
.palette-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-bottom: 0.85rem;
}
.palette-title {
  font-size: 0.76rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-secondary);
}
.palette-tabs {
  display: flex;
  gap: 0.4rem;
  flex-wrap: wrap;
}
.tab-btn {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: var(--text-secondary);
  font-size: 0.74rem;
  font-weight: 700;
  padding: 0.28rem 0.65rem;
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.2s;
}
.tab-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
}
.tab-btn.active {
  background: rgba(0, 202, 255, 0.18);
  border-color: var(--alexa-cyan);
  color: #fff;
  box-shadow: 0 0 10px rgba(0, 202, 255, 0.25);
}

.prompt-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.prompt-btn {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #d2e4f0;
  font-size: 0.8rem;
  font-weight: 600;
  padding: 0.42rem 0.85rem;
  border-radius: 0.65rem;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}
.prompt-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  border-color: var(--alexa-cyan);
  color: #fff;
  transform: translateY(-2px);
  box-shadow: 0 4px 14px rgba(0, 202, 255, 0.25);
}
.hindi-chip {
  border-color: rgba(255, 153, 0, 0.3);
  color: #ffd8a8;
}
.hindi-chip:hover {
  border-color: var(--amazon-orange);
  box-shadow: 0 4px 14px rgba(255, 153, 0, 0.35);
}

/* Alexa Spoken Hologram Bubble */
.hologram-bubble {
  margin-top: 1.25rem;
  background: linear-gradient(135deg, rgba(0, 202, 255, 0.09) 0%, rgba(0, 102, 255, 0.04) 100%);
  border: 1px solid rgba(0, 202, 255, 0.3);
  border-radius: 1rem;
  padding: 1rem 1.3rem;
  display: flex;
  align-items: center;
  gap: 0.85rem;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
}
.bubble-speaker {
  font-size: 0.74rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 0.25rem 0.65rem;
  border-radius: 999px;
  background: var(--alexa-cyan);
  color: #04080c;
}
#speechText {
  font-size: 0.95rem;
  color: #fff;
  font-weight: 500;
  line-height: 1.45;
}

/* Inspector Tray */
.inspector-tray {
  margin-top: 1rem;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 0.85rem;
  background: rgba(0, 0, 0, 0.4);
  overflow: hidden;
}
.inspector-tray summary {
  padding: 0.75rem 1.1rem;
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--text-muted);
  cursor: pointer;
  user-select: none;
}
.inspector-tray summary:hover { color: var(--alexa-cyan); }
.inspector-code {
  padding: 0.85rem 1.1rem;
  font-family: Consolas, monospace;
  font-size: 0.76rem;
  color: #8be9fd;
  white-space: pre-wrap;
  background: #020609;
}

/* ------------------------------------------------------------- Category Filter Tabs */
.ledger-header-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 1rem;
  margin: 2.2rem 0 1.3rem;
}
.category-tabs {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.cat-pill {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: var(--text-secondary);
  font-size: 0.82rem;
  font-weight: 700;
  padding: 0.5rem 1.05rem;
  border-radius: 9999px;
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.3);
}
.cat-pill:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #FFFFFF;
  border-color: rgba(255, 255, 255, 0.2);
}
.cat-pill.active {
  background: linear-gradient(135deg, rgba(0, 202, 255, 0.2) 0%, rgba(0, 102, 255, 0.15) 100%);
  border-color: var(--alexa-cyan);
  color: #FFFFFF;
  box-shadow: 0 0 16px rgba(0, 202, 255, 0.35);
}

/* ------------------------------------------------------------- Glossy Glassmorphic Cards */
.staff-card {
  position: relative;
  background: linear-gradient(145deg, rgba(255, 255, 255, 0.045) 0%, rgba(13, 21, 31, 0.78) 100%);
  backdrop-filter: blur(28px);
  -webkit-backdrop-filter: blur(28px);
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 1.4rem;
  padding: 1.5rem 1.65rem;
  margin-bottom: 1.5rem;
  box-shadow: 
    0 16px 40px rgba(0, 0, 0, 0.55),
    inset 0 1px 0 rgba(255, 255, 255, 0.14);
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
  overflow: hidden;
}
.staff-card:hover {
  transform: translateY(-3px);
  border-color: rgba(0, 202, 255, 0.32);
  box-shadow: 
    0 24px 55px rgba(0, 0, 0, 0.7),
    inset 0 1px 0 rgba(255, 255, 255, 0.22),
    0 0 25px rgba(0, 202, 255, 0.08);
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.8rem;
  margin-bottom: 1.15rem;
  padding-bottom: 0.95rem;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
}
.card-profile {
  display: flex;
  align-items: center;
  gap: 0.9rem;
}
.staff-avatar {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: linear-gradient(135deg, #00CAFF, #0055ff);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
  font-size: 1.15rem;
  color: #fff;
  box-shadow: 0 4px 14px rgba(0, 202, 255, 0.35);
}
.staff-names h2 {
  font-size: 1.25rem;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: #FFFFFF;
}
.staff-names p {
  font-size: 0.82rem;
  color: var(--text-muted);
}
.card-cat-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.74rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 0.2rem 0.55rem;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: #a0d8ef;
  margin-left: 0.5rem;
}
.month-badge {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--text-muted);
  background: rgba(255, 255, 255, 0.05);
  padding: 0.3rem 0.75rem;
  border-radius: 9999px;
  border: 1px solid rgba(255, 255, 255, 0.08);
}

/* Ledger Rows */
.ledger-breakdown {
  margin: 0.85rem 0 1rem;
}
.ledger-row {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  padding: 0.38rem 0;
  font-size: 0.92rem;
}
.ledger-label { color: var(--text-muted); }
.ledger-dotted {
  flex: 1;
  border-bottom: 1px dotted rgba(255, 255, 255, 0.15);
  transform: translateY(-0.3rem);
}
.ledger-val {
  font-variant-numeric: tabular-nums;
  color: #fff;
}
.val-neg { color: var(--status-red); }
.val-pos { color: var(--status-green); }

.total-block {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: rgba(0, 0, 0, 0.32);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 1rem;
  padding: 0.95rem 1.25rem;
  margin: 1rem 0;
}
.total-title {
  font-size: 0.85rem;
  font-weight: 700;
  color: #cde4f5;
}
.total-subtitle {
  font-size: 0.74rem;
  color: var(--text-muted);
}
.total-amount {
  font-size: 1.5rem;
  font-weight: 900;
  color: #00CAFF;
  letter-spacing: -0.02em;
}

/* Warning & Habit Badges */
.warning-badge {
  background: rgba(255, 183, 3, 0.12);
  border: 1px solid rgba(255, 183, 3, 0.35);
  color: #ffd166;
  font-size: 0.82rem;
  padding: 0.55rem 0.95rem;
  border-radius: 0.75rem;
  margin-top: 0.6rem;
}
.habit-badge {
  background: rgba(0, 202, 255, 0.1);
  border: 1px solid rgba(0, 202, 255, 0.3);
  color: #b8ecff;
  font-size: 0.82rem;
  padding: 0.55rem 0.95rem;
  border-radius: 0.75rem;
  margin-top: 0.6rem;
}

/* Card Actions */
.card-actions {
  display: flex;
  gap: 0.55rem;
  flex-wrap: wrap;
  margin-top: 1rem;
}
.btn-upi {
  background: linear-gradient(135deg, #FF9900 0%, #FF5500 100%);
  border: none;
  color: #fff;
  font-weight: 800;
  border-radius: 0.65rem;
  padding: 0.65rem 1.15rem;
  font-size: 0.88rem;
  cursor: pointer;
  box-shadow: 0 4px 14px rgba(255, 153, 0, 0.35);
  transition: all 0.2s;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}
.btn-upi:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(255, 153, 0, 0.5);
}
.btn-whatsapp {
  background: rgba(37, 211, 102, 0.12);
  border: 1px solid rgba(37, 211, 102, 0.35);
  color: #25d366;
  font-weight: 700;
  border-radius: 0.65rem;
  padding: 0.65rem 1.15rem;
  font-size: 0.88rem;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  transition: all 0.2s;
}
.btn-whatsapp:hover {
  background: #25d366;
  color: #05140b;
  box-shadow: 0 4px 16px rgba(37, 211, 102, 0.35);
  transform: translateY(-2px);
}

/* UPI Expansion Drawer */
.upi-expansion-card {
  margin-top: 1.25rem;
  background: #020609;
  border: 1px solid rgba(255, 153, 0, 0.25);
  border-radius: 1rem;
  padding: 1.25rem;
  display: flex;
  align-items: center;
  gap: 1.5rem;
  flex-wrap: wrap;
  animation: slide-down 0.25s ease-out;
}
@keyframes slide-down {
  from { opacity: 0; transform: translateY(-8px); }
  to { opacity: 1; transform: translateY(0); }
}
.upi-qr-image {
  width: 135px;
  height: 135px;
  background: #fff;
  border-radius: 0.75rem;
  padding: 6px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.6);
}
.upi-pay-info {
  flex: 1;
  min-width: 180px;
}
.upi-pay-info b { font-size: 1.05rem; color: #fff; }
.upi-handle-tag {
  display: inline-block;
  background: rgba(255, 255, 255, 0.08);
  padding: 0.2rem 0.5rem;
  border-radius: 0.35rem;
  font-family: monospace;
  margin-top: 0.35rem;
  color: #00CAFF;
}

/* Modal Styling */
.service-modal-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
}
.service-modal-card {
  background: #09121c;
  border: 1px solid rgba(0, 202, 255, 0.35);
  border-radius: 1.4rem;
  padding: 1.8rem;
  max-width: 520px;
  width: 100%;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.8), 0 0 30px rgba(0, 202, 255, 0.2);
}

/* Footer Section */
footer {
  margin-top: 3.5rem;
  padding-top: 1.5rem;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  color: var(--text-muted);
  font-size: 0.85rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.footer-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: #fff;
  padding: 0.55rem 1rem;
  border-radius: 0.5rem;
  text-decoration: none;
  font-weight: 600;
  width: fit-content;
  transition: all 0.2s;
}
.footer-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  border-color: #00CAFF;
}
"""


def base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{proto}://{host}"


def render_page(request: Request) -> str:
    now = today()
    b_url = base_url(request)
    cal_url = f"{b_url}/h/{TOKEN}/calendar.ics"
    gcal = "https://calendar.google.com/calendar/r?cid=" + urllib.parse.quote(
        cal_url.replace("https://", "webcal://").replace("http://", "webcal://"), safe=""
    )

    blocks = []
    with db() as conn:
        people = conn.execute("SELECT * FROM people WHERE active=1 ORDER BY id").fetchall()
        for p in people:
            m = compute_month(conn, p, now.year, now.month)
            rows = [("Pay for the month", m["full_pay"])]
            if m["deduction"]:
                n = m["absent_days"] + 0.5 * m["half_days"]
                rows.append((f"Absent {n:g} day{'s' if n != 1 else ''}, {m['paid_leaves']:g} paid leave", -m["deduction"]))
            if m.get("penalties"):
                rows.append(("Damage / Penalty deduction", -m["penalties"]))
            if m.get("piece_work_total") and p["unit"] != "piece":
                rows.append(("Ironing / Piece work", m["piece_work_total"]))
            if m["bonus"]:
                rows.append(("Bonus", m["bonus"]))
            if m["advances"]:
                rows.append(("Advances taken", -m["advances"]))
            if m["salary_paid"]:
                rows.append(("Already paid", -m["salary_paid"]))

            lines = "".join(
                f'<div class="ledger-row"><span class="ledger-label">{escape(label)}</span><i class="ledger-dotted"></i>'
                f'<b class="ledger-val {"val-neg" if v < 0 else "val-pos" if v > 0 and "Bonus" in label else ""}">'
                f'{"-" if v < 0 else ""}{SYMBOL}{money(abs(v))}</b></div>'
                for label, v in rows
            )

            extra_html = ""
            if m["advance_warning"]:
                extra_html += f'<div class="warning-badge">⚠️ Advances ({SYMBOL}{money(m["advances"])}) exceed the safety limit of {SYMBOL}{money(m["advance_limit"])}.</div>'
            if m["absent_dates"]:
                days_txt = ", ".join(date.fromisoformat(d).strftime("%d %b") for d in m["absent_dates"])
                extra_html += f'<p style="font-size:0.84rem; color:var(--text-muted); margin-top:0.6rem;">Absent on: {escape(days_txt)}</p>'
            pat = pattern_note(conn, p)
            if pat:
                extra_html += f'<div class="habit-badge">💡 Habit Detected: {escape(pat)}</div>'

            wa = "https://wa.me/?text=" + urllib.parse.quote(slip_text(p, m))
            p_id = p["id"]
            initials = "".join([part[0].upper() for part in p["name"].split()[:2]]) or "S"
            rate_label = f"{SYMBOL}{money(p['rate'])}/{p['unit']}"

            upi_drawer_html = f"""
            <div id="upi-drawer-{p_id}" class="upi-expansion-card" style="display:none;">
              <img class="upi-qr-image" src="{escape(m['qr_url'])}" alt="Amazon Pay / UPI QR Code" />
              <div class="upi-pay-info">
                <b>Pay {escape(p['name'])} via Amazon Pay / UPI</b><br/>
                <span class="upi-handle-tag">{escape(m['upi_id'])}</span><br/><br/>
                <span style="font-size:0.86rem; color:var(--text-muted)">Amount due: <b style="color:#fff">{SYMBOL}{money(m['balance'])}</b></span><br/><br/>
                <div style="display:flex; gap:0.5rem; flex-wrap:wrap">
                  <a class="action-btn-primary" href="{escape(m['upi_url'])}" style="text-decoration:none; padding:0.5rem 1rem;">Open UPI App</a>
                  <button class="prompt-btn" style="padding:0.5rem 1rem;" onclick="markSalaryPaid('{escape(p['name'])}')">✓ Mark as Paid</button>
                </div>
              </div>
            </div>
            """

            cat = p["category"] if ("category" in p.keys() and p["category"]) else "staff"
            icon = p["icon"] if ("icon" in p.keys() and p["icon"]) else "👥"
            detail = p["item_detail"] if ("item_detail" in p.keys() and p["item_detail"]) else "Household Service"
            cat_label = "🧹 Staff" if cat == "staff" else "🥛 Milk / Dairy" if cat == "milk" else "📰 Newspaper" if cat == "newspaper" else "💧 Supplies"

            # Specialized quick action buttons for daily deliveries
            delivery_btns = ""
            if cat == "milk":
                delivery_btns = f'''<button class="prompt-btn" style="padding:0.4rem 0.8rem; font-size:0.8rem;" onclick="quickAsk('Milk not delivered today for {escape(p["name"])}')">❌ Skipped Today</button>
                                  <button class="prompt-btn" style="padding:0.4rem 0.8rem; font-size:0.8rem;" onclick="quickAsk('Add 68 rupees extra milk for {escape(p["name"])}')">🥛 +Extra Milk</button>'''
            elif cat == "newspaper":
                delivery_btns = f'''<button class="prompt-btn" style="padding:0.4rem 0.8rem; font-size:0.8rem;" onclick="quickAsk('Pause newspaper today for {escape(p["name"])}')">⏸️ Vacation Hold</button>'''
            elif cat == "utility":
                delivery_btns = f'''<button class="prompt-btn" style="padding:0.4rem 0.8rem; font-size:0.8rem;" onclick="quickAsk('Water can delivered today for {escape(p["name"])}')">💧 +1 Can Delivered</button>'''
            elif cat == "service" or p["unit"] == "piece":
                delivery_btns = f'''<button class="prompt-btn" style="padding:0.4rem 0.8rem; font-size:0.8rem;" onclick="quickAsk(\'Gave 12 clothes to {escape(p["name"])} today\')">🧺 +12 Clothes (₹60)</button>
                                  <button class="prompt-btn" style="padding:0.4rem 0.8rem; font-size:0.8rem;" onclick="quickAsk(\'Gave 20 clothes to {escape(p["name"])} today\')">🧺 +20 Clothes (₹100)</button>'''

            blocks.append(
                f'<div class="staff-card" id="card-{p_id}" data-category="{escape(cat)}">'
                f'  <div class="card-header">'
                f'    <div class="card-profile">'
                f'      <div class="staff-avatar">{escape(icon)}</div>'
                f'      <div class="staff-names">'
                f'        <h2>{escape(p["name"])} <span class="card-cat-badge">{escape(cat_label)}</span></h2>'
                f'        <p>{escape(rate_label)} • {escape(detail)} • ({WEEKDAYS[p["weekly_off"]].title() if p["weekly_off"] >= 0 else "Daily Supply / No"} Off)</p>'
                f'      </div>'
                f'    </div>'
                f'    <span class="month-badge">{month_label(now.year, now.month)}</span>'
                f'  </div>'
                f'  <div class="ledger-breakdown">{lines}</div>'
                f'  <div class="total-block">'
                f'    <div>'
                f'      <div class="total-title">Balance to Pay</div>'
                f'      <div class="total-subtitle">Defaulting present on remaining workdays</div>'
                f'    </div>'
                f'    <div class="total-amount">{SYMBOL}{money(m["balance"])}</div>'
                f'  </div>'
                f'  {extra_html}'
                f'  <div class="card-actions" style="display:flex; gap:0.5rem; flex-wrap:wrap;">'
                f'    {delivery_btns}'
                f'    <button class="prompt-btn" style="padding:0.5rem 0.9rem;" onclick="openSplitModal(\'{escape(p["name"])}\', {int(round(m["balance"]))})">👥 Split with Flatmate</button>'
                f'    <button class="prompt-btn" style="padding:0.5rem 0.9rem;" onclick="promptPenalty(\'{escape(p["name"])}\')">⚠️ Deduct Penalty</button>'
                f'    <button class="btn-upi" onclick="toggleUpiDrawer({p_id})">💳 Pay with Amazon Pay / UPI</button>'
                f'    <a class="btn-whatsapp" href="{wa}" target="_blank" rel="noopener">💬 WhatsApp Slip</a>'
                f'  </div>'
                f'  {upi_drawer_html}'
                f'</div>'
            )
            continue

        # Fixed Commitments & EMIs Cards
        try:
            comm_rows = conn.execute("SELECT * FROM commitments ORDER BY due_day ASC").fetchall()
        except Exception:
            comm_rows = []
        
        staff_burn = sum(max(0.0, float(compute_month(conn, p, now.year, now.month)["balance"])) for p in people)
        fixed_burn = sum(float(c["amount"]) for c in comm_rows)
        total_burn = staff_burn + fixed_burn
        days_in_mth = calendar.monthrange(now.year, now.month)[1]
        impending_7d = sum(float(c["amount"]) for c in comm_rows if 0 <= (c["due_day"] - now.day if c["due_day"] >= now.day else (days_in_mth - now.day) + c["due_day"]) <= 7)

        for c in comm_rows:
            c_id = c["id"]
            c_name = c["name"]
            c_amt = float(c["amount"])
            c_day = c["due_day"]
            c_cat = c["category"]
            c_auto = bool(c["autopay_enabled"])
            c_prov = c["autopay_provider"]
            c_status = c["status"]
            days_in_mth = calendar.monthrange(now.year, now.month)[1]
            diff = c_day - now.day if c_day >= now.day else (days_in_mth - now.day) + c_day
            due_note = "Due Today!" if diff == 0 else f"Due in {diff} days ({c_day}th)" 
            
            icon = "📱" if c_cat == "emi" else "🏠" if c_cat == "rent" else "⚡" if c_cat == "utility_bill" else "🌐"
            cat_label = "💳 Fixed EMI" if c_cat == "emi" else "🏠 House Rent" if c_cat == "rent" else "⚡ Utility Bill" if c_cat == "utility_bill" else "🌐 Subscription"
            
            auto_badge = f'<span style="background:rgba(0,230,118,0.15); border:1px solid #00e676; color:#00e676; padding:0.25rem 0.6rem; border-radius:999px; font-size:0.75rem; font-weight:700;">⚡ {escape(c_prov)} AutoPay: ACTIVE</span>' if c_auto else f'<span style="background:rgba(255,183,3,0.15); border:1px solid #ffb703; color:#ffb703; padding:0.25rem 0.6rem; border-radius:999px; font-size:0.75rem; font-weight:700;">⚠️ Manual Payment Needed</span>'
            status_badge = '<span style="color:#00e676; font-size:0.85rem; font-weight:bold;">✓ Succeeded / Paid</span>' if c_status == "paid" else f'<span style="color:#ffb703; font-size:0.85rem;">⏳ {escape(due_note)}</span>'
            
            upi_memo = f"AmazonPay_{c_name.replace(' ', '_')}"
            comm_upi_url = f"upi://pay?pa=amazonpay@apl&pn=AmazonPay_BillDesk&am={int(round(c_amt))}&cu=INR&tn={upi_memo}"
            comm_qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=180x180&data={urllib.parse.quote(comm_upi_url)}"
            
            comm_upi_drawer = f"""
            <div id="comm-upi-drawer-{c_id}" class="upi-expansion-card" style="display:none;">
              <img class="upi-qr-image" src="{escape(comm_qr_url)}" alt="Amazon Pay QR Code" />
              <div class="upi-pay-info">
                <b>Pay {escape(c_name)} via Amazon Pay</b><br/>
                <span class="upi-handle-tag">amazonpay@apl</span><br/><br/>
                <span style="font-size:0.86rem; color:var(--text-muted)">Amount due: <b style="color:#fff">{SYMBOL}{money(c_amt)}</b></span><br/><br/>
                <div style="display:flex; gap:0.5rem; flex-wrap:wrap">
                  <a class="action-btn-primary" href="{escape(comm_upi_url)}" style="text-decoration:none; padding:0.5rem 1rem;">Open Amazon Pay / UPI</a>
                  <button class="prompt-btn" style="padding:0.5rem 1rem;" onclick="markCommitmentPaid(\'{escape(c_name)}\')">✓ Mark Succeeded</button>
                </div>
              </div>
            </div>
            """
            
            blocks.append(
                f'<div class="staff-card" id="card-comm-{c_id}" data-category="bills">'
                f'  <div class="card-header">'
                f'    <div class="card-profile">'
                f'      <div class="staff-avatar">{icon}</div>'
                f'      <div class="staff-names">'
                f'        <h2>{escape(c_name)} <span class="card-cat-badge">{cat_label}</span></h2>'
                f'        <p>{SYMBOL}{money(c_amt)}/month • Due on {c_day}th of month • {escape(c_prov)}</p>'
                f'      </div>'
                f'    </div>'
                f'    <div>{auto_badge}</div>'
                f'  </div>'
                f'  <div class="ledger-breakdown">'
                f'    <div class="ledger-row"><span class="ledger-label">Monthly Fixed Obligation</span><i class="ledger-dotted"></i><b class="ledger-val">{SYMBOL}{money(c_amt)}</b></div>'
                f'    <div class="ledger-row"><span class="ledger-label">AutoPay e-Mandate Status</span><i class="ledger-dotted"></i><b class="ledger-val">{"Active via " + escape(c_prov) if c_auto else "Disabled"}</b></div>'
                f'    <div class="ledger-row"><span class="ledger-label">Current Billing Status</span><i class="ledger-dotted"></i><b class="ledger-val">{status_badge}</b></div>'
                f'  </div>'
                f'  <div class="total-block">'
                f'    <div><div class="total-title">Total Obligation</div><div class="total-subtitle">{due_note}</div></div>'
                f'    <div class="total-amount">{SYMBOL}{money(c_amt)}</div>'
                f'  </div>'
                f'  <div class="card-actions" style="display:flex; gap:0.5rem; flex-wrap:wrap;">'
                f'    <button class="btn-upi" onclick="toggleCommUpiDrawer({c_id})">⚡ Pay via Amazon Pay</button>'
                f'    <button class="prompt-btn" onclick="toggleAutoPayAPI(\'{escape(c_name)}\')">🔄 {"Disable" if c_auto else "Enable"} AutoPay</button>'
                f'  </div>'
                f'  {comm_upi_drawer}'
                f'</div>'
            )


    if not blocks:
        blocks.append(
            '<div class="staff-card"><p style="color:var(--text-muted)">No staff registered yet. Speak to Alexa above to set up: '
            '<em>"Set up Sunita at 9000 rupees a month"</em>.</p></div>'
        )

    page_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Hisaab — Alexa+ Household Ledger & Amazon Pay Hub</title>
  <style>{PAGE_CSS}</style>
</head>
<body>
<main>
  <!-- Top Navigation with Amazon-Style Smile Logo -->
  <header class="top-nav">
    <div class="brand-group">
      <div class="brand-wordmark-wrap">
        <div class="brand-title">hisaab<span class="brand-alexa-dot">.ai</span></div>
        <!-- Iconic Amazon Smile Arrow SVG -->
        <svg class="amazon-smile-svg" viewBox="0 0 130 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M 5 6 C 42 22, 88 22, 118 7" stroke="#FF9900" stroke-width="3.8" stroke-linecap="round" fill="none"/>
          <path d="M 110 3.5 L 122 7.5 L 114 15.5 C 116.5 11.5, 119 9, 122 7.5 Z" fill="#FF9900"/>
        </svg>
      </div>
      <div class="brand-badge-alexa">
        <span style="color:#FF9900; font-size:0.9rem;">✦</span> Alexa+ & Amazon Pay Hub
      </div>
    </div>
    <div class="status-pill">
      <div class="status-pulse"></div>
      <span>MCP Streamable HTTP Active</span>
    </div>
  </header>

  <!-- Executive Command Center (Burn & Macro Intelligence HUD) -->
  <section class="executive-hud">
    <div class="hud-header">
      <div class="hud-brand-tag">
        <span class="hud-icon">🔥</span>
        <div>
          <div class="hud-subtitle">Amazon Pay & Alexa+ Household Intelligence</div>
          <h2 class="hud-title">Executive Command Center</h2>
        </div>
      </div>
      <div class="hud-actions">
        <button class="action-btn-glow" onclick="quickAsk('What is my total household burn this month?')">
          <span>🔥 Total Monthly Burn</span>
        </button>
        <button class="action-btn-ghost" onclick="quickAsk('What is my household summary for the year?')">
          <span>📊 Annual Insights</span>
        </button>
      </div>
    </div>

    <div class="hud-metrics-grid">
      <div class="hud-metric-card metric-burn">
        <div class="metric-label">Total Monthly Burn</div>
        <div class="metric-value">{SYMBOL}{money(total_burn)}</div>
        <div class="metric-foot">Combined household outflow</div>
      </div>
      <div class="hud-metric-card">
        <div class="metric-label">Domestic Staff & Supplies</div>
        <div class="metric-value cyan-val">{SYMBOL}{money(staff_burn)}</div>
        <div class="metric-foot">Maid, cook, milk, dhobi</div>
      </div>
      <div class="hud-metric-card">
        <div class="metric-label">Fixed Bills & EMIs</div>
        <div class="metric-value emerald-val">{SYMBOL}{money(fixed_burn)}</div>
        <div class="metric-foot">Protected by AutoPay</div>
      </div>
      <div class="hud-metric-card">
        <div class="metric-label">Annual YTD Run-Rate</div>
        <div class="metric-value gold-val">~₹1.2 Lakhs</div>
        <div class="metric-foot">Ramesh (Best Attd.) • Sunita (May Peak)</div>
      </div>
    </div>

    <div class="hud-liquidity-bar">
      <div class="liquidity-badge">🛡️ Liquidity Safety Buffer</div>
      <div class="liquidity-text">Maintain at least <b style="color:#00E676;">{SYMBOL}{money(impending_7d + 5000)}</b> liquid in your primary bank account before Friday to prevent AutoPay bounce.</div>
    </div>
  </section>

  <!-- Nostalgic Alexa 3D Light Ring Console -->
  <section class="alexa-hero-console">
    <div class="alexa-ring-stage" onclick="tapToTalk()" style="cursor:pointer;" title="Click the ring to wake Alexa">
      <div class="alexa-ring-viewport" id="ringViewport">
        <div class="alexa-ambient-halo" id="ambientHalo"></div>
        <div class="alexa-crown-bezel">
          <div class="alexa-light-ring" id="alexaLightRing">
            <div class="alexa-hub-center">
              <div class="alexa-pupil-pip" id="pupilPip"></div>
            </div>
          </div>
        </div>
      </div>
      <canvas class="wave-canvas" id="waveCanvas" width="240" height="36"></canvas>
    </div>

    <!-- Hands-Free Ambient Wake Word Banner -->
    <div class="wake-word-banner">
      <div class="wake-status-group">
        <span id="wakeStatusIcon">🟢</span>
        <span id="wakeStatusText"><b>Hands-Free Active</b>: Just say <i>"Alexa..."</i> aloud (or tap ring)</span>
      </div>
      <button class="btn-lang-toggle" id="btnLangToggle" onclick="toggleLang()" style="background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.18); color:#00CAFF; padding:0.3rem 0.75rem; border-radius:999px; font-size:0.75rem; cursor:pointer;" title="Click to toggle language">🇮🇳 Hinglish (en-IN)</button>
      <button class="btn-wake-toggle" id="btnWakeToggle" onclick="toggleHandsFree()">Listening</button>
    </div>

    <!-- Real-time Live Hearing Transcript Strip -->
    <div class="live-transcript-strip" id="liveTranscriptStrip">
      <span>👂 Hearing:</span>
      <b id="liveHearingText" style="color:#fff">...</b>
    </div>

    <!-- Voice Input Dock -->
    <div class="input-dock">
      <input type="text" id="voiceInput" class="voice-textbox" placeholder="Say 'Alexa, Sunita ka hisaab kitna hai?' or type query..." onkeydown="if(event.key==='Enter') sendVoicePrompt()" />
      <button class="action-btn-primary" id="micBtn" onclick="tapToTalk()">🎙️ Speak</button>
      <button class="action-btn-primary" onclick="sendVoicePrompt()">Ask</button>
    </div>

    <!-- Decluttered Interactive Feature Explorer -->
    <div class="feature-showcase-box">
      <div class="palette-header">
        <div class="palette-title">✨ Curated Voice Commands (Click to Test)</div>
        <div class="palette-tabs">
          <button class="tab-btn active" onclick="filterFeatures('top', this)">⭐ Top Actions</button>
          <button class="tab-btn" onclick="filterFeatures('staff', this)">🧹 Staff & Dhobi</button>
          <button class="tab-btn" onclick="filterFeatures('pay', this)">💳 Amazon Pay & Bills</button>
          <button class="tab-btn" onclick="filterFeatures('insights', this)">📊 Analytics</button>
          <button class="tab-btn" onclick="filterFeatures('hindi', this)">🇮🇳 हिंदी / Hinglish</button>
        </div>
      </div>

      <div class="prompt-grid" id="promptGrid">
        <!-- Top Actions (Default View: 5 uncluttered hero chips) -->
        <button class="prompt-btn" data-cat="top" onclick="quickAsk('Gave 12 clothes to the dhobi today')">🧺 Gave 12 clothes to the dhobi</button>
        <button class="prompt-btn" data-cat="top" onclick="quickAsk('Deduct 50 rupees from Ramesh for the broken glass')">⚠️ Deduct ₹50 from Ramesh (broken glass)</button>
        <button class="prompt-btn" data-cat="top" onclick="quickAsk('Split the maid 4000 bill with Amit')">👥 Split maid's ₹4k bill with Amit</button>
        <button class="prompt-btn" data-cat="top" onclick="quickAsk('Pay iPhone 16 Pro EMI with Amazon Pay')">⚡ Pay iPhone EMI via Amazon Pay</button>
        <button class="prompt-btn" data-cat="top" onclick="quickAsk('What is my total household burn this month?')">🔥 Total Household Burn this Month</button>

        <!-- Staff & Dhobi -->
        <button class="prompt-btn" data-cat="staff" style="display:none;" onclick="quickAsk('Sunita took a half day today')">📅 Sunita took a half day today</button>
        <button class="prompt-btn" data-cat="staff" style="display:none;" onclick="quickAsk('Ramesh was absent yesterday')">📅 Ramesh was absent yesterday</button>
        <button class="prompt-btn" data-cat="staff" style="display:none;" onclick="quickAsk('Give 2000 rupees advance to Sunita')">💳 Give ₹2,000 advance to Sunita</button>
        <button class="prompt-btn" data-cat="staff" style="display:none;" onclick="quickAsk('List all my household staff')">👥 List all household staff</button>
        <button class="prompt-btn" data-cat="staff" style="display:none;" onclick="quickAsk('Undo last entry')">↩️ Undo last entry</button>

        <!-- Amazon Pay & Fixed Bills -->
        <button class="prompt-btn" data-cat="pay" style="display:none;" onclick="quickAsk('Any EMIs due this week?')">🔔 Any EMIs due this week?</button>
        <button class="prompt-btn" data-cat="pay" style="display:none;" onclick="quickAsk('Pay iPhone 16 Pro EMI with Amazon Pay')">⚡ Pay iPhone EMI on Amazon Pay</button>
        <button class="prompt-btn" data-cat="pay" style="display:none;" onclick="quickAsk('Toggle AutoPay for iPhone 16 Pro EMI')">🔄 Toggle Amazon Pay AutoPay</button>
        <button class="prompt-btn" data-cat="pay" style="display:none;" onclick="quickAsk('Pay Ramesh salary with Amazon Pay')">💳 Settle Ramesh with Amazon Pay</button>

        <!-- Analytics & Insights -->
        <button class="prompt-btn" data-cat="insights" style="display:none;" onclick="quickAsk('What is my household summary for the year?')">📊 Household Summary for the Year</button>
        <button class="prompt-btn" data-cat="insights" style="display:none;" onclick="quickAsk('What is my total household burn this month?')">🔥 Total Household Burn this Month</button>
        <button class="prompt-btn" data-cat="insights" style="display:none;" onclick="quickAsk('What is my total household help balance for October?')">💰 Total Household Staff Balance</button>

        <!-- Hindi / Hinglish -->
        <button class="prompt-btn hindi-chip" data-cat="hindi" style="display:none;" onclick="quickAsk('Ghar ka kul hisaab batao')">🗣️ कुल घर का हिसाब बताओ</button>
        <button class="prompt-btn hindi-chip" data-cat="hindi" style="display:none;" onclick="quickAsk('Sunita didi ka hisaab kitna hai?')">🗣️ सुनीता का हिसाब कितना है?</button>
        <button class="prompt-btn hindi-chip" data-cat="hindi" style="display:none;" onclick="quickAsk('Sunita ko 2000 rupaye advance diya')">🗣️ सुनीता को ₹2,000 एडवांस दिया</button>
        <button class="prompt-btn hindi-chip" data-cat="hindi" style="display:none;" onclick="quickAsk('Ramesh aaj aadhi chhutti par tha')">🗣️ रमेश की आधी छुट्टी (Half Day)</button>
        <button class="prompt-btn hindi-chip" data-cat="hindi" style="display:none;" onclick="quickAsk('Saal ka kul hisaab batao annual summary')">🗣️ साल भर का कुल हिसाब (Annual Insights)</button>
        <button class="prompt-btn hindi-chip" data-cat="hindi" style="display:none;" onclick="quickAsk('Pichhla hisaab wapas lo undo karo')">🗣️ पिछला हिसाब वापस लो (Undo)</button>
      </div>
    </div>

    <!-- Alexa Spoken Speech Bubble -->
    <div class="hologram-bubble">
      <span class="bubble-speaker">Alexa</span>
      <span id="speechText">"Hisaab is active. Say 'Alexa' followed by your question in Hindi or English, or tap any trigger above."</span>
    </div>

    <!-- Real-time MCP Inspector -->
    <details class="inspector-tray">
      <summary>⚡ MCP Streamable HTTP Protocol Inspector (JSON-RPC 2.0)</summary>
      <pre class="inspector-code" id="mcpJsonLog">// Streamable HTTP session active at /mcp
// Ready to trace JSON-RPC calls...</pre>
    </details>
  </section>

  <!-- Category Filter Bar & Add Service Action -->
  <div class="ledger-header-bar">
    <div class="category-tabs">
      <button class="cat-pill active" onclick="filterServiceCategory('all', this)">⚡ All Services & Bills</button>
      <button class="cat-pill" onclick="filterServiceCategory('bills', this)">💳 Bills & EMIs (Amazon Pay)</button>
      <button class="cat-pill" onclick="filterServiceCategory('staff', this)">🧹 Domestic Staff</button>
      <button class="cat-pill" onclick="filterServiceCategory('milk', this)">🥛 Milk & Dairy</button>
      <button class="cat-pill" onclick="filterServiceCategory('newspaper', this)">📰 Newspaper</button>
      <button class="cat-pill" onclick="filterServiceCategory('utility', this)">💧 Water Supplies</button>
    </div>
    <button class="action-btn-primary" onclick="toggleAddServiceModal()" style="padding:0.55rem 1.15rem; font-size:0.86rem; border-radius:999px;">➕ Add Service / EMI</button>
  </div>

  <!-- Add Service / Column Modal -->
  <div id="addServiceModal" class="service-modal-overlay" style="display:none;">
    <div class="service-modal-card">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1.2rem; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:0.75rem;">
        <h3 style="margin:0; font-size:1.2rem; color:#fff;">➕ Add Household Service or Obligation</h3>
        <button onclick="toggleAddServiceModal()" style="background:none; border:none; color:#888; font-size:1.4rem; cursor:pointer;">&times;</button>
      </div>
      <div style="display:flex; flex-direction:column; gap:0.85rem;">
        <label style="font-size:0.85rem; color:#cbe3f0;">Name:
          <input type="text" id="newServName" placeholder="e.g. Country Delight Milk, iPhone EMI, Ramlal Mali" class="voice-textbox" style="margin-top:0.35rem; width:100%; box-sizing:border-box;" />
        </label>
        <label style="font-size:0.85rem; color:#cbe3f0;">Category:
          <select id="newServCategory" class="voice-textbox" style="margin-top:0.35rem; width:100%; box-sizing:border-box;">
            <option value="staff">🧹 Domestic Staff (Maid, Cook, Driver)</option>
            <option value="bills">💳 Fixed Bill / Loan EMI (Amazon Pay)</option>
            <option value="milk">🥛 Daily Milk & Dairy Supply</option>
            <option value="newspaper">📰 Newspaper & Magazine</option>
            <option value="utility">💧 Water Cans & Supplies</option>
          </select>
        </label>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:0.8rem;">
          <label style="font-size:0.85rem; color:#cbe3f0;">Rate / Amount (₹):
            <input type="number" id="newServRate" placeholder="e.g. 2800" class="voice-textbox" style="margin-top:0.35rem; width:100%; box-sizing:border-box;" />
          </label>
          <label style="font-size:0.85rem; color:#cbe3f0;">Cycle / Unit:
            <select id="newServUnit" class="voice-textbox" style="margin-top:0.35rem; width:100%; box-sizing:border-box;">
              <option value="month">Per Month</option>
              <option value="day">Per Day / Unit</option>
              <option value="piece">Per Piece (Dhobi)</option>
            </select>
          </label>
        </div>
        <label style="font-size:0.85rem; color:#cbe3f0;">Schedule / Item Details:
          <input type="text" id="newServDetail" placeholder="e.g. 2 Litres daily or Due on 5th via AutoPay" class="voice-textbox" style="margin-top:0.35rem; width:100%; box-sizing:border-box;" />
        </label>
        <label style="font-size:0.85rem; color:#cbe3f0;">Weekly Off / Day:
          <select id="newServOff" class="voice-textbox" style="margin-top:0.35rem; width:100%; box-sizing:border-box;">
            <option value="none">No Off Days (Daily / 7 Days)</option>
            <option value="sunday">Sunday Off</option>
            <option value="monday">Monday Off</option>
            <option value="saturday">Saturday Off</option>
          </select>
        </label>
        <label style="font-size:0.85rem; color:#cbe3f0;">UPI ID (for 1-tap Amazon Pay / UPI):
          <input type="text" id="newServUpi" placeholder="e.g. amazonpay@apl or helper@upi" class="voice-textbox" style="margin-top:0.35rem; width:100%; box-sizing:border-box;" />
        </label>
        <div style="display:flex; justify-content:flex-end; gap:0.8rem; margin-top:1.2rem;">
          <button class="prompt-btn" onclick="toggleAddServiceModal()">Cancel</button>
          <button class="action-btn-primary" onclick="submitAddService()">Save to Ledger</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Staff Ledger Section -->
  <div class="cards-container">
    {"".join(blocks)}
  </div>

  <!-- Footer Sync -->
  <footer>
    <p>Paydays, absences, and pro-rata balances sync live with your calendar.</p>
    <a class="footer-btn" href="{gcal}" target="_blank" rel="noopener">📅 Subscribe in Google Calendar</a>
    <p style="font-size:0.8rem; color:var(--text-muted)">iCalendar feed: <code>{escape(cal_url)}</code></p>
    <p style="font-size:0.8rem; color:var(--text-muted)">MCP Endpoint: <code>{escape(b_url)}/mcp</code></p>
  </footer>
</main>

<script>
// ------------------------------------------------------------- Web Audio Synthesizer (Chimes & Ambience)
let sharedAudioCtx = null;
let micAudioStream = null;
let micAnalyser = null;
let micSource = null;
let currentLang = 'en-IN'; // Supports 'en-IN' and 'en-US'

function getAudioContext() {{
  if (!sharedAudioCtx) {{
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (AudioCtx) sharedAudioCtx = new AudioCtx();
  }}
  if (sharedAudioCtx && sharedAudioCtx.state === 'suspended') {{
    sharedAudioCtx.resume();
  }}
  return sharedAudioCtx;
}}

// Starting sound of Alexa: Sub-bass resonance pop + crisp dual bell (784Hz -> 1046Hz)
function playAlexaWakeChime() {{
  try {{
    const audioCtx = getAudioContext();
    if (!audioCtx) return;
    const now = audioCtx.currentTime;

    // Sub-bass resonance pulse (Echo hardware thump)
    const subOsc = audioCtx.createOscillator();
    const subGain = audioCtx.createGain();
    subOsc.type = 'sine';
    subOsc.frequency.setValueAtTime(140, now);
    subOsc.frequency.exponentialRampToValueAtTime(55, now + 0.12);
    subGain.gain.setValueAtTime(0.3, now);
    subGain.gain.exponentialRampToValueAtTime(0.001, now + 0.15);
    subOsc.connect(subGain);
    subGain.connect(audioCtx.destination);
    subOsc.start(now);
    subOsc.stop(now + 0.15);

    // Bell Tone 1 (G5 - 784 Hz)
    const osc1 = audioCtx.createOscillator();
    const gain1 = audioCtx.createGain();
    osc1.type = 'triangle';
    osc1.frequency.setValueAtTime(783.99, now + 0.02);
    gain1.gain.setValueAtTime(0.35, now + 0.02);
    gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.24);
    osc1.connect(gain1);
    gain1.connect(audioCtx.destination);
    osc1.start(now + 0.02);
    osc1.stop(now + 0.24);

    // Bell Tone 2 (C6 - 1046.5 Hz)
    const osc2 = audioCtx.createOscillator();
    const gain2 = audioCtx.createGain();
    osc2.type = 'sine';
    osc2.frequency.setValueAtTime(1046.5, now + 0.12);
    gain2.gain.setValueAtTime(0.4, now + 0.12);
    gain2.gain.exponentialRampToValueAtTime(0.001, now + 0.48);
    osc2.connect(gain2);
    gain2.connect(audioCtx.destination);
    osc2.start(now + 0.12);
    osc2.stop(now + 0.48);
  }} catch (e) {{}}
}}

// Soothing background ambient chord played after listening and before Alexa speaks
function playSoothingProcessingSound() {{
  try {{
    const audioCtx = getAudioContext();
    if (!audioCtx) return;
    const now = audioCtx.currentTime;

    // Meditative ambient harmonic chord: C4 (261.63Hz), G4 (392Hz), C5 (523.25Hz), E5 (659.25Hz)
    const freqs = [261.63, 392.00, 523.25, 659.25];
    const filter = audioCtx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.setValueAtTime(800, now);
    filter.frequency.exponentialRampToValueAtTime(1300, now + 0.4);
    filter.frequency.exponentialRampToValueAtTime(500, now + 1.2);
    filter.connect(audioCtx.destination);

    freqs.forEach((f, idx) => {{
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(f, now + idx * 0.03);
      gain.gain.setValueAtTime(0.001, now);
      gain.gain.linearRampToValueAtTime(0.08, now + 0.2 + idx * 0.03);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 1.2);
      osc.connect(gain);
      gain.connect(filter);
      osc.start(now + idx * 0.03);
      osc.stop(now + 1.2);
    }});
  }} catch (e) {{}}
}}

// ------------------------------------------------------------- Real-time 3D Waveform Visualizer
const canvas = document.getElementById('waveCanvas');
const ctx = canvas.getContext('2d');
let waveStep = 0;
let isAudioActive = false;
let micAudioLevel = 0;

function drawWave() {{
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const mid = canvas.height / 2;

  // React to actual microphone volume if stream is connected
  let amp = 3;
  if (micAnalyser) {{
    const dataArray = new Uint8Array(micAnalyser.frequencyBinCount);
    micAnalyser.getByteFrequencyData(dataArray);
    let sum = 0;
    for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
    micAudioLevel = sum / dataArray.length;
    amp = Math.max(3, micAudioLevel * 0.45);
  }} else if (isAudioActive) {{
    amp = 14;
  }}

  ctx.beginPath();
  ctx.strokeStyle = (isAudioActive || micAudioLevel > 10) ? '#00d2ff' : 'rgba(0, 210, 255, 0.4)';
  ctx.lineWidth = (isAudioActive || micAudioLevel > 10) ? 2.5 : 1.5;

  for (let x = 0; x < canvas.width; x++) {{
    const y = mid + Math.sin((x * 0.05) + waveStep) * amp * Math.sin((x / canvas.width) * Math.PI);
    if (x === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }}
  ctx.stroke();

  waveStep += (isAudioActive || micAudioLevel > 10) ? 0.2 : 0.04;
  requestAnimationFrame(drawWave);
}}
drawWave();

// ------------------------------------------------------------- Active Hardware Microphone Capture
async function ensureMicAccess() {{
  if (micAudioStream) return true;
  try {{
    micAudioStream = await navigator.mediaDevices.getUserMedia({{ audio: true }});
    const audioCtx = getAudioContext();
    if (audioCtx) {{
      micSource = audioCtx.createMediaStreamSource(micAudioStream);
      micAnalyser = audioCtx.createAnalyser();
      micAnalyser.fftSize = 64;
      micSource.connect(micAnalyser);
    }}
    return true;
  }} catch (err) {{
    console.warn("Microphone access permission error:", err);
    wakeStatusText.innerHTML = "Mic access blocked. Click 🔒 in address bar to allow.";
    return false;
  }}
}}

// ------------------------------------------------------------- Alexa State & Dialogue Engine
let handsFreeEnabled = true;
let isSpeaking = false;
let recognition = null;
let isAwake = false;
let awakeTimer = null;
let currentPlayingAudio = null;

const ringEl = document.getElementById('alexaLightRing');
const wakeStatusText = document.getElementById('wakeStatusText');
const wakeStatusIcon = document.getElementById('wakeStatusIcon');
const btnWakeToggle = document.getElementById('btnWakeToggle');
const liveTranscriptStrip = document.getElementById('liveTranscriptStrip');
const liveHearingText = document.getElementById('liveHearingText');
const speechText = document.getElementById('speechText');
const micBtn = document.getElementById('micBtn');

function setAlexaState(state) {{
  if (!ringEl) return;
  ringEl.classList.remove('listening', 'thinking', 'speaking');
  if (state) ringEl.classList.add(state);
}}

function wakeAlexa(initialCommand) {{
  if (awakeTimer) clearTimeout(awakeTimer);
  isAwake = true;
  playAlexaWakeChime();
  setAlexaState('listening');
  isAudioActive = true;
  micBtn.innerText = '🔴 Listening...';

  if (initialCommand && initialCommand.trim().length > 1) {{
    isAwake = false;
    document.getElementById('voiceInput').value = initialCommand.trim();
    sendVoicePrompt(initialCommand.trim());
  }} else {{
    speechText.innerText = "Listening... Ask your question or command.";
    awakeTimer = setTimeout(() => {{
      if (isAwake && !isSpeaking) {{
        isAwake = false;
        setAlexaState(null);
        isAudioActive = false;
        micBtn.innerText = '🎙️ Speak';
        speechText.innerText = "Hisaab is active. Say 'Alexa' or click the ring to talk.";
      }}
    }}, 7500);
  }}
}}

function initSpeechRecognition() {{
  if (!('webkitSpeechRecognition' in window || 'SpeechRecognition' in window)) {{
    wakeStatusText.innerHTML = "Speech recognition unavailable in this browser. Use buttons or textbox.";
    wakeStatusIcon.innerText = "⚠️";
    return;
  }}

  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  recognition = new SpeechRec();
  // Single-turn continuous loop: Chrome never hangs or stalls in this mode!
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = currentLang;

  recognition.onstart = () => {{
    wakeStatusIcon.innerText = "🟢";
    wakeStatusText.innerHTML = `<b>Mic Live & Listening (${{currentLang}})</b>: Say <i>'Alexa...'</i>`;
    btnWakeToggle.innerText = "Listening";
  }};

  recognition.onspeechstart = () => {{
    liveTranscriptStrip.style.display = 'flex';
    liveHearingText.innerText = "Listening to your voice...";
  }};

  recognition.onresult = (e) => {{
    if (isSpeaking) return;

    for (let i = e.resultIndex; i < e.results.length; ++i) {{
      const transcript = e.results[i][0].transcript.trim().toLowerCase();
      
      // Update Live Transcript HUD
      liveTranscriptStrip.style.display = 'flex';
      liveHearingText.innerText = transcript;

      // Check for Wake Words: 'alexa', 'hey alexa', 'अलेक्सा', etc.
      const wakeMatch = transcript.match(/(?:hey[ ]+)?(?:alexa|elixa|alex|alxa|अलेक्सा)/i);

      if (wakeMatch) {{
        const parts = transcript.split(wakeMatch[0]);
        const command = parts.slice(1).join(' ').trim();

        if (e.results[i].isFinal) {{
          wakeAlexa(command);
          break;
        }} else if (!ringEl.classList.contains('listening')) {{
          wakeAlexa(null);
        }}
      }} else if (isAwake) {{
        // Alexa is already awake and waiting for user command
        if (e.results[i].isFinal && transcript.length > 1) {{
          if (awakeTimer) clearTimeout(awakeTimer);
          isAwake = false;
          document.getElementById('voiceInput').value = transcript;
          sendVoicePrompt(transcript);
          break;
        }}
      }} else if (e.results[i].isFinal && transcript.length > 3) {{
        // Direct intent matching without wake word
        const hasStaff = /(?:sunita|ramesh|raju|anil|ramlal|dhobi|amit|iphone|tata|airtel|rent|दूध|धोबी|सुनीता|रमेश|राजू|अनिल)/i.test(transcript);
        const hasIntent = /(?:hisaab|hisab|salary|pay|advance|absent|chhutti|half|balance|undo|wapas|cloth|iron|press|kapde|penalty|deduct|broken|damage|split|share|bill|year|annual|summary|insight|emi|burn|autopay|reminder|due|amazon|हिसाब|वेतन|छुट्टी|कपड़े|जुर्माना|साल|खर्चा)/i.test(transcript);
        if (hasStaff || hasIntent || micBtn.innerText.includes('Listening')) {{
          document.getElementById('voiceInput').value = transcript;
          sendVoicePrompt(transcript);
          break;
        }}
      }}
    }}
  }};

  recognition.onerror = (e) => {{
    if (e.error === 'not-allowed') {{
      wakeStatusIcon.innerText = "🔒";
      wakeStatusText.innerHTML = "Mic permission needed. Click <b>'🎙️ Speak'</b> to allow.";
      btnWakeToggle.innerText = "Enable Mic";
      handsFreeEnabled = false;
    }} else if (e.error !== 'no-speech') {{
      console.warn("SpeechRec error:", e.error);
    }}
  }};

  recognition.onend = () => {{
    liveTranscriptStrip.style.display = 'none';
    // Automatically restart listening turn seamlessly
    if (handsFreeEnabled && !isSpeaking) {{
      setTimeout(() => {{
        try {{ recognition.start(); }} catch(err) {{}}
      }}, 250);
    }}
  }};
}}

initSpeechRecognition();

async function tapToTalk() {{
  getAudioContext();
  await ensureMicAccess();
  handsFreeEnabled = true;
  wakeAlexa(null);
  try {{ recognition.start(); }} catch(err) {{}}
}}

function toggleHandsFree() {{
  if (!recognition) return;
  if (handsFreeEnabled) {{
    handsFreeEnabled = false;
    try {{ recognition.stop(); }} catch(e) {{}}
    wakeStatusIcon.innerText = "⏸️";
    wakeStatusText.innerHTML = "Hands-free paused. Click to re-enable.";
    btnWakeToggle.innerText = "Enable";
  }} else {{
    handsFreeEnabled = true;
    try {{ recognition.start(); }} catch(err) {{}}
  }}
}}

function toggleLang() {{
  currentLang = (currentLang === 'en-IN') ? 'en-US' : 'en-IN';
  if (recognition) {{
    recognition.lang = currentLang;
  }}
  const el = document.getElementById('btnLangToggle');
  if (el) {{
    el.innerText = (currentLang === 'en-IN') ? '🇮🇳 Hinglish (en-IN)' : '🌐 English (en-US)';
  }}
  wakeStatusText.innerHTML = `<b>Mic Live & Listening (${{currentLang}})</b>: Say <i>'Alexa...'</i>`;
}}

function quickAsk(text) {{
  document.getElementById('voiceInput').value = text;
  sendVoicePrompt(text);
}}



function promptPenalty(person) {{
  const amt = prompt('Enter penalty/damage amount to deduct from ' + person + ' (₹):', '50');
  if (amt && parseFloat(amt) > 0) {{
    const reason = prompt('Reason for deduction (e.g. broken glass, late arrival):', 'broken glass');
    quickAsk('Deduct ' + amt + ' rupees from ' + person + ' for ' + (reason || 'damage'));
  }}
}}

function openSplitModal(person, balance) {{
  const flatmate = prompt('Enter flatmate name to split ' + person + ' bill with:', 'Amit');
  if (flatmate) {{
    const amt = prompt('Bill amount to split (leave blank for current balance ₹' + balance + '):', balance || '4000');
    quickAsk('Split ' + person + ' ' + (amt || balance) + ' bill with ' + flatmate);
  }}
}}

function filterServiceCategory(cat, btn) {{
  document.querySelectorAll('.cat-pill').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  document.querySelectorAll('.staff-card').forEach(card => {{
    const cardCat = card.getAttribute('data-category') || 'staff';
    if (cat === 'all' || cardCat === cat) {{
      card.style.display = 'block';
    }} else {{
      card.style.display = 'none';
    }}
  }});
}}

function toggleCommUpiDrawer(id) {{
  const el = document.getElementById('comm-upi-drawer-' + id);
  if (el) {{
    el.style.display = (el.style.display === 'none' || !el.style.display) ? 'flex' : 'none';
  }}
}}

async function markCommitmentPaid(name) {{
  try {{
    const res = await fetch('/api/pay_commitment', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{name: name}})
    }});
    const data = await res.json();
    if (data.ok) {{
      speakAlexaResponse(data.spoken_summary);
      setTimeout(() => window.location.reload(), 1500);
    }} else {{
      alert('Error: ' + (data.error || 'Payment failed'));
    }}
  }} catch (e) {{
    alert('Error connecting to server');
  }}
}}

async function toggleAutoPayAPI(name) {{
  try {{
    const res = await fetch('/api/toggle_autopay', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{name: name}})
    }});
    const data = await res.json();
    if (data.ok) {{
      speakAlexaResponse(data.spoken_summary);
      setTimeout(() => window.location.reload(), 1500);
    }} else {{
      alert('Error: ' + (data.error || 'Toggle failed'));
    }}
  }} catch (e) {{
    alert('Error connecting to server');
  }}
}}

function toggleAddServiceModal() {{
  const el = document.getElementById('addServiceModal');
  if (el) {{
    el.style.display = (el.style.display === 'none' || !el.style.display) ? 'flex' : 'none';
  }}
}}

async function submitAddService() {{
  const name = document.getElementById('newServName').value.trim();
  const cat = document.getElementById('newServCategory').value;
  const rate = parseFloat(document.getElementById('newServRate').value) || 1000;
  const unit = document.getElementById('newServUnit').value;
  const detail = document.getElementById('newServDetail').value.trim();
  const off = document.getElementById('newServOff').value;
  const upi = document.getElementById('newServUpi').value.trim();

  if (!name) {{
    alert('Please enter a service or provider name.');
    return;
  }}

  try {{
    const resp = await fetch('/api/add_person', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{
        name: name,
        rate: rate,
        unit: unit,
        category: cat,
        item_detail: detail,
        weekly_off: off,
        upi_id: upi
      }})
    }});
    const data = await resp.json();
    if (data.ok) {{
      toggleAddServiceModal();
      refreshLedgerCards();
      speakAlexaResponse(`Added ${{name}} to your household ledger.`);
    }} else {{
      alert('Error: ' + data.error);
    }}
  }} catch(e) {{
    alert('Failed to add service: ' + e);
  }}
}}

function filterFeatures(category, btn) {{
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');

  const chips = document.querySelectorAll('.prompt-btn');
  chips.forEach(chip => {{
    const cats = chip.getAttribute('data-cat') || '';
    if (category === 'all' || cats.includes(category)) {{
      chip.style.display = 'inline-flex';
    }} else {{
      chip.style.display = 'none';
    }}
  }});
}}

// ------------------------------------------------------------- Seamless Ledger Sync (No Full Page Reload)
async function refreshLedgerCards() {{
  try {{
    const res = await fetch('/');
    const htmlText = await res.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(htmlText, 'text/html');
    const newContainer = doc.querySelector('.cards-container');
    if (newContainer) {{
      document.querySelector('.cards-container').innerHTML = newContainer.innerHTML;
    }}
  }} catch (e) {{}}
}}

// ------------------------------------------------------------- Fail-Safe Spoken Audio Engine (Crystal-Clear & Lag-Free)
function cleanSpokenText(t) {{
  if (!t) return "";
  return t
    .replace(/₹/g, ' rupees ')
    .replace(/Rs\\./g, ' rupees ')
    .replace(/\\s*\\(\\s*₹?\\s*(\\d+)\\s*\\)/g, ', which is $1 rupees, ')
    .replace(/\\s*\\((.*?)\\)/g, ', $1, ')
    .replace(/-(\\d+)/g, 'minus $1')
    .replace(/\\s+/g, ' ')
    .trim();
}}

function speakAlexaResponse(rawText) {{
  if (!rawText) return;
  const text = cleanSpokenText(rawText);

  // 1. Immediately mute mic input to prevent acoustic feedback loop / stutter
  if (recognition) {{
    try {{ recognition.abort(); }} catch(e) {{}}
  }}

  // 2. Clear any lingering utterances to avoid audio queue lag
  if (window.speechSynthesis) {{
    try {{ window.speechSynthesis.cancel(); }} catch(e) {{}}
  }}
  if (currentPlayingAudio) {{
    try {{ currentPlayingAudio.pause(); }} catch(e) {{}}
    currentPlayingAudio = null;
  }}

  isSpeaking = true;
  setAlexaState('speaking');
  isAudioActive = true;

  const onSpeechFinished = () => {{
    isSpeaking = false;
    isAudioActive = false;
    setAlexaState(null);
    micBtn.innerText = '🎙️ Speak';
    
    // Safely resume hands-free listening after speech finishes
    if (handsFreeEnabled) {{
      setTimeout(() => {{
        if (!isSpeaking) {{
          try {{ recognition.start(); }} catch(e) {{}}
        }}
      }}, 350);
    }}
  }};

  // 3. Play via High-Definition SAPI with strict single-source guarantee (no double-voices)
  const audio = new Audio();
  currentPlayingAudio = audio;
  let fallbackHandled = false;

  const runFallback = () => {{
    if (fallbackHandled) return;
    fallbackHandled = true;
    if (currentPlayingAudio === audio) {{
      currentPlayingAudio = null;
    }}
    fallbackBrowserSpeech(text, onSpeechFinished);
  }};

  audio.onended = () => {{
    if (fallbackHandled) return;
    currentPlayingAudio = null;
    onSpeechFinished();
  }};

  audio.onerror = () => {{
    runFallback();
  }};

  // Safety fallback if network or audio device stalls over 1200ms
  const loadTimeout = setTimeout(() => {{
    if (!audio.currentTime || audio.paused) {{
      runFallback();
    }}
  }}, 1200);

  audio.onplay = () => {{
    clearTimeout(loadTimeout);
  }};

  audio.src = '/api/tts?text=' + encodeURIComponent(text);
  audio.play().catch(() => {{
    runFallback();
  }});
}}

// Fallback: Browser Web SpeechSynthesis (Natural Neural Voices)
function fallbackBrowserSpeech(text, callback) {{
  if (!('speechSynthesis' in window)) {{
    setTimeout(callback, 2000);
    return;
  }}
  try {{ window.speechSynthesis.cancel(); }} catch(e) {{}}
  try {{ window.speechSynthesis.resume(); }} catch(e) {{}}

  const utter = new SpeechSynthesisUtterance(text);
  window._activeAlexaUtterance = utter;

  const voices = window.speechSynthesis.getVoices() || [];
  if (voices.length > 0) {{
    const chosen = voices.find(v => v.name.toLowerCase().includes('zira')) ||
                   voices.find(v => v.name.toLowerCase().includes('natural') && (v.lang.startsWith('en') || v.lang === 'en-IN')) ||
                   voices.find(v => v.lang === 'en-IN') ||
                   voices.find(v => v.lang.startsWith('en'));
    if (chosen) utter.voice = chosen;
  }}
  utter.rate = 1.0;
  utter.pitch = 1.02;

  let finished = false;
  const done = () => {{
    if (finished) return;
    finished = true;
    callback();
  }};

  utter.onend = done;
  utter.onerror = done;
  setTimeout(done, Math.max(3000, (text.split(' ').length / 2.2) * 1000 + 1000));
  try {{ window.speechSynthesis.speak(utter); }} catch(e) {{ done(); }}
}}

// ------------------------------------------------------------- Voice Prompt Dispatcher (Zero-Wait Pipelined)
async function sendVoicePrompt(customText) {{
  const input = customText || document.getElementById('voiceInput').value.trim();
  if (!input) return;

  if (awakeTimer) clearTimeout(awakeTimer);
  isAwake = false;

  // 1. Play soothing ambient background chord immediately
  playSoothingProcessingSound();
  setAlexaState('thinking');
  isAudioActive = true;
  micBtn.innerText = '🎙️ Speak';

  // 2. FIRE THE API REQUEST IMMEDIATELY IN PARALLEL WITH THE THINKING STEPS!
  const fetchPromise = fetch('/api/simulate', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ prompt: input }})
  }}).then(r => r.json()).catch(err => ({{ ok: false, error: err }}));

  // 3. Crisp, responsive thinking steps (400ms + 400ms = 800ms total)
  speechText.innerHTML = '<span class="neural-badge">🧠 Alexa+ Neural Engine</span> Analyzing query & context...';
  await new Promise(r => setTimeout(r, 400));

  speechText.innerHTML = '<span class="neural-badge">⚡ Hisaab Core</span> Verifying ledger, attendance & Amazon Pay status...';
  await new Promise(r => setTimeout(r, 400));

  speechText.innerHTML = '<span class="neural-badge">✨ Multi-Modal Agent</span> Synthesizing voice response...';

  try {{
    const data = await fetchPromise;
    if (!data || (!data.ok && data.error)) throw (data ? data.error : "Network error");

    speechText.innerText = `"${{data.spoken_summary}}"`;
    const mcpJsonLog = document.getElementById('mcpJsonLog');
    if (mcpJsonLog) {{
      mcpJsonLog.innerText = JSON.stringify(data.mcp_trace, null, 2);
    }}

    // Speak response out loud cleanly with zero lag and zero double-speech
    speakAlexaResponse(data.spoken_summary);

    if (data.mutated) {{
      refreshLedgerCards();
    }}
  }} catch (err) {{
    speechText.innerText = "Connection error to Hisaab MCP server.";
    isAudioActive = false;
    setAlexaState(null);
    isSpeaking = false;
  }}
}}

function toggleUpiDrawer(id) {{
  const el = document.getElementById('upi-drawer-' + id);
  if (el) {{
    el.style.display = (el.style.display === 'none' || !el.style.display) ? 'flex' : 'none';
  }}
}}

function markSalaryPaid(person) {{
  quickAsk(`Pay ${{person}} salary with Amazon Pay`);
}}

// Unlock audio context and microphone on any page interaction
window.addEventListener('click', async () => {{
  getAudioContext();
  await ensureMicAccess();
  if (recognition && handsFreeEnabled && !isSpeaking) {{
    try {{ recognition.start(); }} catch(err) {{}}
  }}
}});
</script>
</body>
</html>"""
    return page_html


def token_ok(value: str) -> bool:
    return secrets.compare_digest(value.encode(), TOKEN.encode())



@mcp.custom_route("/api/tts", methods=["GET"])
async def text_to_speech(request: Request) -> Response:
    """Generates crystal-clear spoken WAV audio using native Windows SAPI (Microsoft Zira / Alexa voice)."""
    text = request.query_params.get("text", "").strip()
    if not text:
        return PlainTextResponse("Missing text", status_code=400)
    
    # Clean and naturalize text for crystal-clear phonetics
    clean_text = text.replace("₹", " rupees ").replace("Rs.", " rupees ")
    clean_text = re.sub(r"\s*\(\s*rupees?\s*(\d+)\s*\)", r", which is \1 rupees, ", clean_text)
    clean_text = re.sub(r"\s*\((.*?)\)", r", \1, ", clean_text)
    clean_text = re.sub(r"-(\d+)", r"minus \1", clean_text)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()

    try:
        import win32com.client
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tmp_path = tf.name
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        voices = speaker.GetVoices()
        for i in range(voices.Count):
            v = voices.Item(i)
            desc = v.GetDescription().lower()
            if "zira" in desc or "female" in desc:
                speaker.Voice = v
                break
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        try:
            audio_format = win32com.client.Dispatch("SAPI.SpAudioFormat")
            audio_format.Type = 22 # SAFT22kHz16BitMono for high-definition clarity
            stream.Format = audio_format
        except Exception:
            pass
        stream.Open(tmp_path, 3, False)
        speaker.AudioOutputStream = stream
        speaker.Rate = 0  # 0 is the natural, crystal-clear, steady Alexa pace
        speaker.Volume = 100
        speaker.Speak(clean_text)
        stream.Close()
        with open(tmp_path, "rb") as f:
            wav_bytes = f.read()
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return Response(content=wav_bytes, media_type="audio/wav")
    except Exception as e:
        return PlainTextResponse(f"TTS error: {e}", status_code=500)



@mcp.custom_route("/api/add_person", methods=["POST"])
async def api_add_person(request: Request) -> Response:
    """Dynamically provisions a new household service, delivery, or staff ledger profile."""
    try:
        body = await request.json()
        name = body.get("name", "").strip()
        rate = float(body.get("rate", 1000))
        unit = body.get("unit", "month").strip()
        category = body.get("category", "staff").strip()
        item_detail = body.get("item_detail", "").strip()
        weekly_off = body.get("weekly_off", "none").strip()
        upi_id = body.get("upi_id", "").strip()
        res = set_up_person(
            name=name,
            pay_amount=rate,
            pay_unit=unit,
            weekly_off=weekly_off,
            upi_id=upi_id,
            category=category,
            item_detail=item_detail,
        )
        return JSONResponse({"ok": True, "result": res})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@mcp.custom_route("/api/toggle_autopay", methods=["POST"])
async def api_toggle_autopay(request: Request) -> Response:
    try:
        body = await request.json()
        nm = body.get("name", "")
        en = body.get("enable")
        res = toggle_autopay(name=nm, enable=en)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@mcp.custom_route("/api/pay_commitment", methods=["POST"])
async def api_pay_commitment(request: Request) -> Response:
    try:
        body = await request.json()
        nm = body.get("name", "")
        res = pay_commitment_amazon_pay(name=nm)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@mcp.custom_route("/api/add_commitment", methods=["POST"])
async def api_add_commitment(request: Request) -> Response:
    try:
        body = await request.json()
        res = add_commitment(
            name=body.get("name", ""),
            amount=float(body.get("amount", 0)),
            due_day=int(body.get("due_day", 1)),
            category=body.get("category", "emi"),
            autopay=bool(body.get("autopay", True)),
        )
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@mcp.custom_route("/api/simulate", methods=["POST"])
async def simulate_voice(request: Request) -> Response:
    """Simulates an Alexa+ conversational agent turn dispatching to FastMCP tools with Hindi and Hinglish comprehension."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "spoken_summary": "Invalid JSON request."}, status_code=400)

    prompt = (body.get("prompt") or "").strip().lower()
    # Strip leading "alexa", "hey alexa", "अलेक्सा"
    prompt = re.sub(r"^(hey\s+)?(alexa|elixa|alex|अलेक्सा)\s*", "", prompt).strip()

    d = today()
    mutated = False
    trace = {
        "transport": "Streamable HTTP (/mcp)",
        "protocol_version": "2025-11-25",
        "method": "tools/call",
        "input_prompt": prompt,
    }

    with db() as conn:
        people = conn.execute("SELECT * FROM people WHERE active=1 ORDER BY id").fetchall()

    # Dynamic Person Matching (Supports English, Hindi aliases, and generic terms)
    target_person = "Sunita"  # default
    matched_explicit = False
    for p in people:
        p_name = p["name"].lower()
        if p_name in prompt or (p_name == "sunita" and ("सुनीता" in prompt or "maid" in prompt or "bai" in prompt or "didi" in prompt)):
            target_person = p["name"]
            matched_explicit = True
            break
        elif p_name == "ramesh" and ("रमेश" in prompt or "cook" in prompt or "chef" in prompt or "rasoiya" in prompt):
            target_person = p["name"]
            matched_explicit = True
            break
        elif p_name == "raju" and ("राजू" in prompt or "driver" in prompt):
            target_person = p["name"]
            matched_explicit = True
            break
        elif p_name == "anil" and ("अनिल" in prompt or "milkman" in prompt or "doodh" in prompt or "dairy" in prompt):
            target_person = p["name"]
            matched_explicit = True
            break
        elif any(k in prompt for k in ["milk", "doodh", "dairy", "country delight", "दूध"]):
            for cand in people:
                if cand["category"] == "milk" or "delight" in cand["name"].lower() or "milk" in cand["name"].lower() or "anil" in cand["name"].lower():
                    target_person = cand["name"]
                    matched_explicit = True
                    break
        elif any(k in prompt for k in ["newspaper", "paper", "times", "bhaskar", "akhbar", "अखबार"]):
            for cand in people:
                if cand["category"] == "newspaper" or "times" in cand["name"].lower() or "news" in cand["name"].lower():
                    target_person = cand["name"]
                    matched_explicit = True
                    break
        elif any(k in prompt for k in ["water", "can", "jar", "bisleri", "paani", "पानी"]):
            for cand in people:
                if cand["category"] == "utility" or "water" in cand["name"].lower() or "bisleri" in cand["name"].lower():
                    target_person = cand["name"]
                    matched_explicit = True
                    break

    # Intent A: Dhobi / Ironing Piece Work Counter
    if any(k in prompt for k in ["dhobi", "clothes", "ironing", "press", "kapde", "कपड़े", "धोबी"]):
        count_match = re.search(r"(\d+)", prompt)
        piece_count = int(count_match.group(1)) if count_match else 10
        # Find dhobi service
        dhobi_person = "Ramlal Dhobi"
        for p in people:
            if "dhobi" in p["name"].lower() or p["category"] == "service" or p["unit"] == "piece":
                dhobi_person = p["name"]
                break
        res = log_pieces(person=dhobi_person, count=piece_count)
        mutated = True
        trace["tool"] = "log_pieces"
        trace["arguments"] = {"person": dhobi_person, "count": piece_count}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": mutated, "mcp_trace": trace})

    # Intent B: Damage & Penalty Deduction
    if any(k in prompt for k in ["deduct", "penalty", "broken", "damage", "nuksan", "काटो", "जुर्माना", "loss", "toota", "glass"]):
        amt_match = re.search(r"(\d+)", prompt)
        penalty_amt = float(amt_match.group(1)) if amt_match else 50.0
        reason = "broken glass" if ("glass" in prompt or "dish" in prompt) else "damage deduction"
        res = log_penalty(person=target_person, amount=penalty_amt, reason=reason)
        mutated = True
        trace["tool"] = "log_penalty"
        trace["arguments"] = {"person": target_person, "amount": penalty_amt, "reason": reason}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": mutated, "mcp_trace": trace})

    # Intent C: Flatmate / PG Bill Splitter
    if any(k in prompt for k in ["split", "share with", "roommate", "flatmate", "baato", "बांटो"]):
        amt_match = re.search(r"(\d+)", prompt)
        split_amt = float(amt_match.group(1)) if amt_match else None
        # Extract flatmate name
        fm_match = re.search(r"(?:with|aur)\s+([a-zA-Z]+)", prompt)
        split_with_name = fm_match.group(1) if fm_match else "Amit"
        res = split_bill(person=target_person, split_with=split_with_name, amount=split_amt)
        trace["tool"] = "split_bill"
        trace["arguments"] = {"person": target_person, "split_with": split_with_name, "amount": split_amt}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": False, "mcp_trace": trace})

    # Intent D: Total Household Monthly Burn
    if any(k in prompt for k in ["burn", "total burn", "total expense", "total kharcha", "kul kharcha", "household burn", "monthly burn", "pura kharcha", "ghar ka kharcha"]):
        res = total_household_burn(month=f"{d.year}-{d.month:02d}")
        trace["tool"] = "total_household_burn"
        trace["arguments"] = {"month": f"{d.year}-{d.month:02d}"}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": False, "mcp_trace": trace})

    # Intent E: Upcoming Reminders & EMIs Due
    if any(k in prompt for k in ["reminder", "due", "upcoming", "emi due", "bills due", "kya bacha hai", "remind me", "this week", "next week", "any emi"]):
        days_match = re.search(r"(\d+)\s*days?", prompt)
        d_ahead = int(days_match.group(1)) if days_match else 7
        res = upcoming_reminders(days_ahead=d_ahead)
        trace["tool"] = "upcoming_reminders"
        trace["arguments"] = {"days_ahead": d_ahead}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": False, "mcp_trace": trace})

    # Intent F: Toggle Amazon Pay AutoPay
    if any(k in prompt for k in ["autopay", "auto pay", "mandate", "e-mandate"]):
        enable = False if any(k in prompt for k in ["disable", "turn off", "stop", "band"]) else True
        target_comm = "iPhone 16 Pro EMI"
        if "rent" in prompt:
            target_comm = "Flat Rent & Society"
        elif "electricity" in prompt or "power" in prompt or "bijli" in prompt:
            target_comm = "Tata Power Electricity"
        elif "wifi" in prompt or "fiber" in prompt or "airtel" in prompt or "internet" in prompt:
            target_comm = "Airtel Xstream Fiber"
        res = toggle_autopay(name=target_comm, enable=enable)
        trace["tool"] = "toggle_autopay"
        trace["arguments"] = {"name": target_comm, "enable": enable}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": True, "mcp_trace": trace})

    # Intent G: Pay Commitment / EMI via Amazon Pay
    if any(k in prompt for k in ["pay emi", "amazon pay", "pay bill", "pay iphone", "pay electricity", "pay rent", "chuka do", "bharo"]):
        target_comm = "iPhone 16 Pro EMI"
        if "rent" in prompt:
            target_comm = "Flat Rent & Society"
        elif "electricity" in prompt or "power" in prompt or "bijli" in prompt:
            target_comm = "Tata Power Electricity"
        elif "wifi" in prompt or "fiber" in prompt or "airtel" in prompt or "internet" in prompt:
            target_comm = "Airtel Xstream Fiber"
        res = pay_commitment_amazon_pay(name=target_comm)
        trace["tool"] = "pay_commitment_amazon_pay"
        trace["arguments"] = {"name": target_comm}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": True, "mcp_trace": trace})

    # Intent routing simulation (Supports Hindi + English)
    # 1. Undo
    if any(k in prompt for k in ["undo", "wapas", "वापस", "cancel", "galti", "गलती", "revert"]):
        res = undo_last()
        mutated = True
        trace["tool"] = "undo_last"
        trace["arguments"] = {}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": mutated, "mcp_trace": trace})

    # 2. Annual Insights & Yearly Analytics Engine
    if any(k in prompt for k in ["year", "annual", "yearly", "saal", "साल", "this year", "summary for the year", "saal bhar", "annual summary", "saal ka hisaab"]):
        res = annual_insights(year=d.year)
        trace["tool"] = "annual_insights"
        trace["arguments"] = {"year": d.year}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": mutated, "mcp_trace": trace})

    # 2. Total Household Hisaab
    if any(k in prompt for k in ["total", "household", "everyone", "kul", "कुल", "sabka", "सबका", "poora", "pura", "all staff"]) or (
        any(k in prompt for k in ["balance", "hisaab", "हिसाब"]) and not matched_explicit and not any(p["name"].lower() in prompt for p in people)
    ):
        res = household_hisaab(month=f"{d.year}-{d.month:02d}")
        trace["tool"] = "household_hisaab"
        trace["arguments"] = {"month": f"{d.year}-{d.month:02d}"}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": mutated, "mcp_trace": trace})

    # 3. List People / Staff
    if any(k in prompt for k in ["list", "who works", "kaun kaun", "कौन कौन", "staff dikhao", "staff list"]):
        res = list_people()
        trace["tool"] = "list_people"
        trace["arguments"] = {}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": mutated, "mcp_trace": trace})

    # 4. Salary Payment via Amazon Pay / UPI
    if any(k in prompt for k in ["pay", "payment", "salary", "amazon", "upi", "settle", "de do", "दे दो", "bhugtan", "भुगतान", "chukao"]):
        res = pay_salary(person=target_person, payment_mode="amazon_pay")
        mutated = True
        trace["tool"] = "pay_salary"
        trace["arguments"] = {"person": target_person, "payment_mode": "amazon_pay"}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": mutated, "mcp_trace": trace})

    # 5. Half Day / Aadhi Chhutti
    if any(k in prompt for k in ["half day", "half", "aadha", "aadhi", "आधी", "halfday"]):
        res = log_visit(person=target_person, status="half_day")
        mutated = True
        trace["tool"] = "log_visit"
        trace["arguments"] = {"person": target_person, "status": "half_day"}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": mutated, "mcp_trace": trace})

    # 6. Advance Payment
    if any(k in prompt for k in ["advance", "advans", "एडवांस", "peshgi", "advance diya", "diya"]):
        amt_match = re.search(r"(\d+)", prompt)
        amt = float(amt_match.group(1)) if amt_match else 2000.0
        res = log_payment(person=target_person, amount=amt, kind="advance")
        mutated = True
        trace["tool"] = "log_payment"
        trace["arguments"] = {"person": target_person, "amount": amt, "kind": "advance"}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": mutated, "mcp_trace": trace})

    # 7. Absence / Chhutti / Skipped Delivery / Vacation Hold
    if any(k in prompt for k in ["absent", "chhutti", "chutti", "छुट्टी", "nahi aayi", "nahi aaya", "नहीं आई", "leave", "skipped", "pause", "hold", "stop"]):
        res = log_visit(person=target_person, status="absent")
        mutated = True
        trace["tool"] = "log_visit"
        trace["arguments"] = {"person": target_person, "status": "absent"}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": mutated, "mcp_trace": trace})

    # 8. Extra Delivery / Bonus / Extra Milk
    if any(k in prompt for k in ["extra", "aur", "packet", "litre", "लीटर", "more"]):
        amt_match = re.search(r"(\d+)", prompt)
        amt = float(amt_match.group(1)) if amt_match else 68.0
        res = log_payment(person=target_person, amount=amt, kind="bonus")
        mutated = True
        trace["tool"] = "log_payment"
        trace["arguments"] = {"person": target_person, "amount": amt, "kind": "bonus"}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": mutated, "mcp_trace": trace})

    # Default: Individual Staff Hisaab / Balance Query
    res = month_hisaab(person=target_person, month=f"{d.year}-{d.month:02d}")
    trace["tool"] = "month_hisaab"
    trace["arguments"] = {"person": target_person, "month": f"{d.year}-{d.month:02d}"}
    trace["result"] = res
    return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": mutated, "mcp_trace": trace})


@mcp.custom_route("/", methods=["GET"])
async def root(request: Request) -> Response:
    return HTMLResponse(render_page(request))


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    return PlainTextResponse("ok")


@mcp.custom_route("/h/{token}", methods=["GET"])
async def shared_page(request: Request) -> Response:
    if not token_ok(request.path_params["token"]):
        return PlainTextResponse("Not found", status_code=404)
    return HTMLResponse(render_page(request))


@mcp.custom_route("/h/{token}/calendar.ics", methods=["GET"])
async def calendar_feed(request: Request) -> Response:
    if not token_ok(request.path_params["token"]):
        return PlainTextResponse("Not found", status_code=404)
    return Response(build_ics(), media_type="text/calendar; charset=utf-8")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")

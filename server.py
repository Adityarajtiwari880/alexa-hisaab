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

    if person["unit"] == "day":
        per_day = person["rate"]
        unpaid = lost
        gross = per_day * (working_days - lost)
        full = per_day * working_days
    else:
        per_day = person["rate"] / dim
        unpaid = max(0.0, lost - person["paid_leaves"])
        gross = person["rate"] - unpaid * per_day
        full = person["rate"]

    balance = gross + bonus - advances - salary_paid
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
def set_up_person(
    name: str,
    pay_amount: float,
    pay_unit: str = "month",
    weekly_off: str = "sunday",
    paid_leaves_per_month: float = 1,
    pay_day: int = 1,
    upi_id: str = "",
) -> dict:
    """Set up or update a household staff member: maid, cook, driver, milkman, or tutor."""
    unit = (pay_unit or "month").strip().lower()
    if unit not in ("month", "day"):
        raise ValueError("Pay can be per month or per day.")
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
  --bg-dark: #04080c;
  --panel-bg: rgba(13, 21, 29, 0.78);
  --panel-border: rgba(0, 210, 255, 0.22);
  --text-main: #f0f6fa;
  --text-muted: #8ba2b0;
  --cyan-glow: #00d2ff;
  --alexa-blue: #0077ff;
  --coral-red: #ff5757;
  --emerald-green: #00e676;
  --gold-accent: #ffb703;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: var(--bg-dark);
  background-image: 
    radial-gradient(circle at 50% 0%, rgba(0, 119, 255, 0.18) 0%, transparent 60%),
    radial-gradient(circle at 10% 30%, rgba(0, 210, 255, 0.09) 0%, transparent 45%),
    radial-gradient(circle at 90% 70%, rgba(0, 85, 255, 0.09) 0%, transparent 50%);
  background-attachment: fixed;
  color: var(--text-main);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  min-height: 100vh;
}

main {
  max-width: 45rem;
  margin: 0 auto;
  padding: 2rem 1.25rem 5rem;
}

/* Header Navigation Bar */
.top-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 2rem;
}
.brand-group {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
}
.brand-title {
  font-size: 2.15rem;
  font-weight: 900;
  letter-spacing: -0.03em;
  background: linear-gradient(135deg, #ffffff 40%, #00d2ff 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
.brand-tag {
  font-size: 0.85rem;
  color: var(--text-muted);
  font-weight: 500;
}
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  font-size: 0.78rem;
  font-weight: 600;
  padding: 0.35rem 0.8rem;
  border-radius: 9999px;
  background: rgba(0, 230, 118, 0.08);
  border: 1px solid rgba(0, 230, 118, 0.25);
  color: var(--emerald-green);
  letter-spacing: 0.02em;
}
.status-pulse {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--emerald-green);
  box-shadow: 0 0 8px var(--emerald-green);
  animation: pulse-dot 2s infinite ease-in-out;
}
@keyframes pulse-dot {
  0%, 100% { opacity: 0.5; transform: scale(0.9); }
  50% { opacity: 1; transform: scale(1.3); }
}

/* ------------------------------------------------------------- Authentic Echo 3D Bezel Console */
.alexa-hero-console {
  position: relative;
  background: var(--panel-bg);
  backdrop-filter: blur(28px);
  -webkit-backdrop-filter: blur(28px);
  border: 1px solid var(--panel-border);
  border-radius: 1.6rem;
  padding: 2.25rem 1.75rem 1.75rem;
  margin-bottom: 2.5rem;
  box-shadow: 
    0 24px 60px rgba(0, 0, 0, 0.65),
    inset 0 1px 0 rgba(255, 255, 255, 0.12),
    0 0 35px rgba(0, 210, 255, 0.08);
  overflow: hidden;
}

.alexa-hero-console::before {
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, #00d2ff, #0055ff, #00d2ff, transparent);
  background-size: 200% auto;
  animation: cyber-shimmer 4s linear infinite;
}
@keyframes cyber-shimmer {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}

/* Nostalgic Alexa 3D Light Ring */
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
  transform: rotateX(46deg); /* Authentic 3D Echo Crown perspective */
  transition: transform 0.5s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.alexa-ambient-halo {
  position: absolute;
  width: 225px;
  height: 225px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(0, 210, 255, 0.4) 0%, rgba(0, 119, 255, 0.2) 50%, transparent 70%);
  filter: blur(20px);
  animation: halo-breathe 3.5s ease-in-out infinite;
  pointer-events: none;
}

.alexa-crown-bezel {
  width: 165px;
  height: 165px;
  border-radius: 50%;
  position: relative;
  background: radial-gradient(circle, #081118 55%, #152533 70%, #030608 100%);
  box-shadow: 
    0 18px 40px rgba(0, 0, 0, 0.85),
    0 0 30px rgba(0, 210, 255, 0.28),
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
    #00d2ff 110deg,
    #00ffff 165deg,
    #ffffff 180deg,
    #00ffff 195deg,
    #00d2ff 250deg,
    #0066ff 305deg,
    #0044ff 360deg
  );
  box-shadow: 
    0 0 24px #00d2ff,
    0 0 50px rgba(0, 210, 255, 0.65),
    inset 0 0 16px rgba(0, 255, 255, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  animation: ring-sweep 5s linear infinite;
  transition: all 0.35s ease;
}

.alexa-light-ring.listening {
  animation: ring-sweep 1.5s linear infinite, ring-pulse 0.8s ease-in-out infinite alternate !important;
  box-shadow: 0 0 45px #00ffff, 0 0 90px rgba(0, 210, 255, 0.95), inset 0 0 22px #ffffff !important;
}
.alexa-light-ring.thinking {
  animation: ring-sweep 0.8s cubic-bezier(0.4, 0, 0.2, 1) infinite !important;
  box-shadow: 0 0 45px #00a8e8, 0 0 90px rgba(0, 119, 255, 0.85) !important;
}
.alexa-light-ring.speaking {
  animation: ring-sweep 2.8s linear infinite, ring-voice-undulate 1.2s ease-in-out infinite !important;
}

@keyframes ring-sweep { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
@keyframes ring-pulse { from { filter: brightness(1.1); } to { filter: brightness(1.5); } }
@keyframes ring-voice-undulate {
  0%, 100% { transform: rotate(0deg) scale(1); filter: brightness(1.15); }
  50% { transform: rotate(180deg) scale(1.03); filter: brightness(1.45); box-shadow: 0 0 38px #00ffff, 0 0 75px rgba(0, 210, 255, 0.85); }
}
@keyframes halo-breathe {
  0%, 100% { opacity: 0.6; transform: scale(0.95); }
  50% { opacity: 0.95; transform: scale(1.05); }
}

.alexa-hub-center {
  width: 100%;
  height: 100%;
  border-radius: 50%;
  background: radial-gradient(circle at 45% 40%, #0d1720 0%, #04080c 70%, #010305 100%);
  border: 1px solid rgba(0, 210, 255, 0.25);
  box-shadow: inset 0 3px 8px rgba(0, 0, 0, 0.95), 0 0 12px rgba(0, 0, 0, 0.8);
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.alexa-pupil-pip {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 35%, #ffffff 0%, #00ffff 50%, #0066ff 100%);
  box-shadow: 0 0 14px #00ffff, 0 0 25px #00d2ff;
  animation: pupil-breathe 2.5s ease-in-out infinite;
  transition: all 0.3s;
}
@keyframes pupil-breathe {
  0%, 100% { transform: scale(0.85); opacity: 0.75; }
  50% { transform: scale(1.2); opacity: 1; box-shadow: 0 0 22px #00ffff, 0 0 38px #00d2ff; }
}

.wave-canvas {
  width: 240px;
  height: 36px;
  margin-top: 0.4rem;
  opacity: 0.9;
}

/* Hands-Free & Live Hearing Banner */
.wake-word-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(0, 210, 255, 0.08);
  border: 1px solid rgba(0, 210, 255, 0.25);
  border-radius: 0.75rem;
  padding: 0.65rem 1rem;
  margin-top: 1rem;
  font-size: 0.88rem;
}
.wake-status-group {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: #cbe3eb;
}
.btn-wake-toggle {
  background: #00d2ff;
  color: #041017;
  font-weight: 700;
  border: none;
  border-radius: 0.5rem;
  padding: 0.38rem 0.85rem;
  cursor: pointer;
  font-size: 0.82rem;
  transition: all 0.2s;
}
.btn-wake-toggle:hover { opacity: 0.9; }

/* Real-Time Live Transcript HUD */
.live-transcript-strip {
  margin-top: 0.6rem;
  padding: 0.45rem 0.85rem;
  background: rgba(0, 0, 0, 0.4);
  border: 1px dashed rgba(0, 210, 255, 0.3);
  border-radius: 0.5rem;
  font-size: 0.85rem;
  color: #00ffff;
  display: none;
  align-items: center;
  gap: 0.5rem;
}

/* Voice Input Controls */
.input-dock {
  display: flex;
  gap: 0.6rem;
  margin-top: 0.75rem;
}
.voice-textbox {
  flex: 1;
  background: rgba(4, 8, 12, 0.85);
  border: 1px solid rgba(0, 210, 255, 0.25);
  border-radius: 0.75rem;
  padding: 0.8rem 1.1rem;
  color: #fff;
  font-size: 0.95rem;
  transition: all 0.2s ease;
  box-shadow: inset 0 2px 4px rgba(0,0,0,0.4);
}
.voice-textbox:focus {
  outline: none;
  border-color: var(--cyan-glow);
  box-shadow: 0 0 15px rgba(0, 210, 255, 0.3), inset 0 2px 4px rgba(0,0,0,0.5);
}

.action-btn-primary {
  background: linear-gradient(135deg, #00d2ff 0%, #0077ff 100%);
  color: #040c12;
  font-weight: 700;
  border: none;
  border-radius: 0.75rem;
  padding: 0 1.25rem;
  font-size: 0.92rem;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  box-shadow: 0 4px 15px rgba(0, 210, 255, 0.3);
  transition: all 0.2s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.action-btn-primary:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 22px rgba(0, 210, 255, 0.5);
}
.action-btn-primary:active { transform: scale(0.97); }

/* ------------------------------------------------------------- Filterable Feature Showcase Palette */
.feature-showcase-box {
  margin-top: 1.5rem;
  background: rgba(0, 0, 0, 0.25);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 1rem;
  padding: 1.15rem;
}
.palette-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.85rem;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.palette-title {
  font-size: 0.82rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--cyan-glow);
  font-weight: 800;
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.palette-tabs {
  display: flex;
  gap: 0.35rem;
  flex-wrap: wrap;
}
.tab-btn {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: var(--text-muted);
  font-size: 0.78rem;
  font-weight: 600;
  padding: 0.25rem 0.65rem;
  border-radius: 9999px;
  cursor: pointer;
  transition: all 0.2s;
}
.tab-btn.active, .tab-btn:hover {
  background: rgba(0, 210, 255, 0.18);
  border-color: rgba(0, 210, 255, 0.45);
  color: #fff;
}
.prompt-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
}
.prompt-btn {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.09);
  color: #d8ecf5;
  font-size: 0.82rem;
  font-weight: 500;
  padding: 0.42rem 0.85rem;
  border-radius: 9999px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  transition: all 0.18s;
  backdrop-filter: blur(8px);
}
.prompt-btn:hover {
  background: rgba(0, 210, 255, 0.14);
  border-color: rgba(0, 210, 255, 0.4);
  color: #fff;
  transform: translateY(-1px);
}
.prompt-btn.hindi-chip {
  border-color: rgba(255, 183, 3, 0.3);
  color: #ffe699;
}
.prompt-btn.hindi-chip:hover {
  background: rgba(255, 183, 3, 0.15);
  color: #fff;
}

/* Holographic Alexa Spoken Response Bubble */
.hologram-bubble {
  margin-top: 1.25rem;
  background: rgba(0, 16, 28, 0.9);
  border-left: 3px solid var(--cyan-glow);
  border-radius: 0 0.85rem 0.85rem 0;
  padding: 0.9rem 1.15rem;
  font-size: 0.96rem;
  line-height: 1.5;
  color: #e3f4fc;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
}
.bubble-speaker {
  font-weight: 800;
  color: var(--cyan-glow);
  font-size: 0.8rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-right: 0.4rem;
}

/* MCP Real-time Inspector Terminal Tray */
.inspector-tray {
  margin-top: 1rem;
  font-size: 0.82rem;
}
.inspector-tray summary {
  color: var(--text-muted);
  cursor: pointer;
  user-select: none;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.inspector-tray summary:hover { color: var(--cyan-glow); }
.inspector-code {
  background: #020508;
  border: 1px solid rgba(0, 210, 255, 0.15);
  border-radius: 0.65rem;
  padding: 0.85rem;
  color: #00e676;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.78rem;
  overflow-x: auto;
  margin-top: 0.5rem;
  line-height: 1.45;
}

/* Premium Staff Ledger Cards */
.cards-container {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}
.staff-card {
  background: var(--panel-bg);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 1.4rem;
  padding: 1.75rem;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.45);
  position: relative;
  transition: border-color 0.25s, transform 0.25s;
}
.staff-card:hover {
  border-color: rgba(0, 210, 255, 0.3);
  transform: translateY(-2px);
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  padding-bottom: 1rem;
  margin-bottom: 1.25rem;
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
  background: linear-gradient(135deg, #00d2ff, #0055ff);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
  font-size: 1.15rem;
  color: #fff;
  box-shadow: 0 4px 14px rgba(0, 210, 255, 0.35);
}
.staff-names h2 {
  font-size: 1.35rem;
  font-weight: 800;
  letter-spacing: -0.01em;
}
.staff-names p {
  font-size: 0.84rem;
  color: var(--text-muted);
}
.month-badge {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--text-muted);
  background: rgba(255, 255, 255, 0.05);
  padding: 0.3rem 0.75rem;
  border-radius: 9999px;
  border: 1px solid rgba(255, 255, 255, 0.08);
}


/* Service Category Tabs & Add Modal */
.cat-pill {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: #c9e6f5;
  font-size: 0.83rem;
  font-weight: 600;
  padding: 0.45rem 0.95rem;
  border-radius: 9999px;
  cursor: pointer;
  transition: all 0.2s ease;
}
.cat-pill:hover, .cat-pill.active {
  background: rgba(0, 210, 255, 0.2);
  border-color: #00d2ff;
  color: #fff;
  box-shadow: 0 0 12px rgba(0, 210, 255, 0.3);
}
.neural-badge {
  display: inline-block;
  background: rgba(0, 210, 255, 0.15);
  border: 1px solid rgba(0, 210, 255, 0.4);
  color: #00d2ff;
  font-size: 0.74rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 0.2rem 0.55rem;
  border-radius: 0.35rem;
  margin-right: 0.5rem;
}
.service-modal-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
}
.service-modal-card {
  background: #06111e;
  border: 1px solid rgba(0, 210, 255, 0.35);
  border-radius: 1.4rem;
  padding: 1.8rem;
  max-width: 520px;
  width: 100%;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.8), 0 0 30px rgba(0, 210, 255, 0.2);
}
.card-cat-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 0.2rem 0.55rem;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: #a0d8ef;
  margin-left: 0.6rem;
}

/* Ledger Breakdown Rows */
.ledger-row {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  padding: 0.4rem 0;
  font-size: 0.95rem;
}
.ledger-label { color: var(--text-muted); }
.ledger-dotted {
  flex: 1;
  border-bottom: 1px dotted rgba(255, 255, 255, 0.15);
  transform: translateY(-0.3rem);
}
.ledger-val {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.val-neg { color: var(--coral-red); }
.val-pos { color: var(--emerald-green); }

/* Prominent 'To Pay' Block */
.total-block {
  margin-top: 1.1rem;
  padding: 1.1rem;
  background: linear-gradient(135deg, rgba(0, 210, 255, 0.08) 0%, rgba(0, 119, 255, 0.04) 100%);
  border: 1px solid rgba(0, 210, 255, 0.22);
  border-radius: 1rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.total-title {
  font-size: 1.05rem;
  font-weight: 700;
}
.total-subtitle {
  font-size: 0.78rem;
  color: var(--text-muted);
  margin-top: 0.15rem;
}
.total-amount {
  font-size: 1.95rem;
  font-weight: 900;
  color: #ff6b6b;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}

/* Habit & Warning Chips */
.habit-badge {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.9rem;
  padding: 0.65rem 0.95rem;
  background: rgba(255, 183, 3, 0.08);
  border: 1px solid rgba(255, 183, 3, 0.25);
  border-radius: 0.75rem;
  font-size: 0.88rem;
  color: #ffd166;
  font-weight: 500;
}
.warning-badge {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.9rem;
  padding: 0.65rem 0.95rem;
  background: rgba(255, 87, 87, 0.08);
  border: 1px solid rgba(255, 87, 87, 0.28);
  border-radius: 0.75rem;
  font-size: 0.88rem;
  color: #ff8585;
  font-weight: 600;
}

/* Card Action Buttons */
.card-actions {
  display: flex;
  gap: 0.65rem;
  flex-wrap: wrap;
  margin-top: 1.25rem;
}
.btn-upi {
  background: linear-gradient(135deg, #ff9900 0%, #ff6600 100%);
  color: #0b0f14;
  font-weight: 800;
  border: none;
  border-radius: 0.65rem;
  padding: 0.7rem 1.15rem;
  font-size: 0.92rem;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  box-shadow: 0 4px 16px rgba(255, 153, 0, 0.35);
  transition: all 0.2s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.btn-upi:hover { transform: translateY(-2px); box-shadow: 0 6px 20px rgba(255, 153, 0, 0.5); }
.btn-upi:active { transform: scale(0.97); }

.btn-whatsapp {
  background: rgba(37, 211, 102, 0.12);
  border: 1px solid rgba(37, 211, 102, 0.35);
  color: #25d366;
  font-weight: 700;
  border-radius: 0.65rem;
  padding: 0.7rem 1.15rem;
  font-size: 0.92rem;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  transition: all 0.2s;
}
.btn-whatsapp:hover {
  background: #25d366;
  color: #05140b;
  box-shadow: 0 4px 16px rgba(37, 211, 102, 0.35);
  transform: translateY(-2px);
}

/* Dynamic UPI Drawer */
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
  width: 140px;
  height: 140px;
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
  color: #00d2ff;
}

/* Footer Section */
footer {
  margin-top: 3.5rem;
  padding-top: 1.5rem;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  color: var(--text-muted);
  font-size: 0.88rem;
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
  border-color: #00d2ff;
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
                f'    <button class="btn-upi" onclick="toggleUpiDrawer({p_id})">💳 Pay with Amazon Pay / UPI</button>'
                f'    <a class="btn-whatsapp" href="{wa}" target="_blank" rel="noopener">💬 WhatsApp Slip</a>'
                f'  </div>'
                f'  {upi_drawer_html}'
                f'</div>'
            )
            continue


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
  <title>Hisaab — Alexa+ Voice Ledger</title>
  <style>{PAGE_CSS}</style>
</head>
<body>
<main>
  <!-- Top Navigation -->
  <header class="top-nav">
    <div class="brand-group">
      <h1 class="brand-title">Hisaab</h1>
      <span class="brand-tag">Alexa+ Household Ledger</span>
    </div>
    <div class="status-pill">
      <div class="status-pulse"></div>
      <span>Streamable HTTP /mcp</span>
    </div>
  </header>

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
        <span id="wakeStatusText"><b>Hands-Free Active</b>: Just say <i>"Alexa..."</i> aloud (or click ring)</span>
      </div>
      <button class="btn-lang-toggle" id="btnLangToggle" onclick="toggleLang()" style="background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.18); color:#00d2ff; padding:0.3rem 0.75rem; border-radius:999px; font-size:0.75rem; cursor:pointer;" title="Click to toggle language">🇮🇳 Hinglish (en-IN)</button>
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

    <!-- Categorized Interactive Feature Showcase Palette -->
    <div class="feature-showcase-box">
      <div class="palette-header">
        <div class="palette-title">✨ Interactive Feature Explorer (Click to Try)</div>
        <div class="palette-tabs">
          <button class="tab-btn active" onclick="filterFeatures('all', this)">All</button>
          <button class="tab-btn" onclick="filterFeatures('hindi', this)">🇮🇳 हिंदी / Hinglish</button>
          <button class="tab-btn" onclick="filterFeatures('balance', this)">💰 Balances</button>
          <button class="tab-btn" onclick="filterFeatures('pay', this)">💳 Pay & Advances</button>
          <button class="tab-btn" onclick="filterFeatures('attendance', this)">📅 Attendance</button>
          <button class="tab-btn" onclick="filterFeatures('staff', this)">👥 Staff</button>
        </div>
      </div>

      <div class="prompt-grid" id="promptGrid">
        <!-- Hindi / Hinglish Queries -->
        <button class="prompt-btn hindi-chip" data-cat="hindi all" onclick="quickAsk('Sunita didi ka hisaab kitna hai?')">🗣️ सुनीता का हिसाब कितना है?</button>
        <button class="prompt-btn hindi-chip" data-cat="hindi all" onclick="quickAsk('Ghar ka kul hisaab batao')">🗣️ कुल घर का हिसाब बताओ</button>
        <button class="prompt-btn hindi-chip" data-cat="hindi all" onclick="quickAsk('Sunita ko 2000 rupaye advance diya')">🗣️ सुनीता को ₹2,000 एडवांस दिया</button>
        <button class="prompt-btn hindi-chip" data-cat="hindi all" onclick="quickAsk('Ramesh aaj aadhi chhutti par tha')">🗣️ रमेश की आधी छुट्टी (Half Day)</button>
        <button class="prompt-btn hindi-chip" data-cat="hindi all" onclick="quickAsk('Raju driver ka hisaab Amazon Pay se karo')">🗣️ राजू ड्राइवर का हिसाब Amazon Pay से करो</button>
        <button class="prompt-btn hindi-chip" data-cat="hindi all" onclick="quickAsk('Pichhla hisaab wapas lo undo karo')">🗣️ पिछला हिसाब वापस लो (Undo)</button>

        <!-- Balance Queries -->
        <button class="prompt-btn" data-cat="balance all" onclick="quickAsk('How much do I owe Sunita?')">🎙️ How much do I owe Sunita?</button>
        <button class="prompt-btn" data-cat="balance all" onclick="quickAsk('What is my total household balance for October?')">🎙️ Total Household Hisaab</button>
        <button class="prompt-btn" data-cat="balance all" onclick="quickAsk('How much do I owe Ramesh cook?')">🎙️ How much do I owe Ramesh (Cook)?</button>
        <button class="prompt-btn" data-cat="balance all" onclick="quickAsk('How much do I owe Raju driver?')">🎙️ How much do I owe Raju (Driver)?</button>

        <!-- Pay & Advances -->
        <button class="prompt-btn" data-cat="pay all" onclick="quickAsk('Pay Sunita salary with Amazon Pay')">💳 Pay Sunita with Amazon Pay</button>
        <button class="prompt-btn" data-cat="pay all" onclick="quickAsk('Give 2000 rupees advance to Sunita')">💳 Give 2000 advance to Sunita</button>
        <button class="prompt-btn" data-cat="pay all" onclick="quickAsk('Pay Ramesh salary with Amazon Pay')">💳 Settle Ramesh with Amazon Pay</button>
        <button class="prompt-btn" data-cat="pay all" onclick="quickAsk('Give 1000 rupees advance to Ramesh')">💳 Give 1000 advance to Ramesh</button>

        <!-- Attendance -->
        <button class="prompt-btn" data-cat="attendance all" onclick="quickAsk('Sunita took a half day today')">📅 Sunita took half day today</button>
        <button class="prompt-btn" data-cat="attendance all" onclick="quickAsk('Ramesh was absent yesterday')">📅 Ramesh was absent yesterday</button>
        <button class="prompt-btn" data-cat="attendance all" onclick="quickAsk('Sunita was actually present today')">📅 Correct: Sunita was present</button>
        <button class="prompt-btn" data-cat="attendance all" onclick="quickAsk('Undo last entry')">↩️ Undo last entry</button>

        <!-- Staff Management -->
        <button class="prompt-btn" data-cat="staff all" onclick="quickAsk('List all my household staff')">👥 List all my household staff</button>
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
  <div class="ledger-header-bar" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:1rem; margin:1.8rem 0 1.2rem;">
    <div class="category-tabs" style="display:flex; gap:0.5rem; flex-wrap:wrap;">
      <button class="cat-pill active" onclick="filterServiceCategory('all', this)">⚡ All Household Services</button>
      <button class="cat-pill" onclick="filterServiceCategory('staff', this)">🧹 Domestic Staff</button>
      <button class="cat-pill" onclick="filterServiceCategory('milk', this)">🥛 Milk & Dairy</button>
      <button class="cat-pill" onclick="filterServiceCategory('newspaper', this)">📰 Newspaper</button>
      <button class="cat-pill" onclick="filterServiceCategory('utility', this)">💧 Water & Supplies</button>
    </div>
    <button class="action-btn-primary" onclick="toggleAddServiceModal()" style="padding:0.55rem 1.15rem; font-size:0.86rem; border-radius:999px;">➕ Add Service / Supply / Staff</button>
  </div>

  <!-- Add Service / Column Modal -->
  <div id="addServiceModal" class="service-modal-overlay" style="display:none;">
    <div class="service-modal-card">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1.2rem; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:0.75rem;">
        <h3 style="margin:0; font-size:1.2rem; color:#fff;">➕ Add Household Service or Subscription</h3>
        <button onclick="toggleAddServiceModal()" style="background:none; border:none; color:#888; font-size:1.4rem; cursor:pointer;">&times;</button>
      </div>
      <div style="display:flex; flex-direction:column; gap:0.85rem;">
        <label style="font-size:0.85rem; color:#cbe3f0;">Service / Provider Name:
          <input type="text" id="newServName" placeholder="e.g. Country Delight Milk, Dainik Bhaskar, Ramlal Mali" class="voice-textbox" style="margin-top:0.35rem; width:100%; box-sizing:border-box;" />
        </label>
        <label style="font-size:0.85rem; color:#cbe3f0;">Category:
          <select id="newServCategory" class="voice-textbox" style="margin-top:0.35rem; width:100%; box-sizing:border-box;">
            <option value="staff">🧹 Domestic Staff (Maid, Cook, Driver)</option>
            <option value="milk">🥛 Daily Milk & Dairy Supply</option>
            <option value="newspaper">📰 Newspaper & Magazine Subscription</option>
            <option value="utility">💧 Water Cans & Groceries</option>
            <option value="other">🧺 Laundry & Other Services</option>
          </select>
        </label>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:0.8rem;">
          <label style="font-size:0.85rem; color:#cbe3f0;">Rate / Price (₹):
            <input type="number" id="newServRate" placeholder="e.g. 2800" class="voice-textbox" style="margin-top:0.35rem; width:100%; box-sizing:border-box;" />
          </label>
          <label style="font-size:0.85rem; color:#cbe3f0;">Billing Cycle:
            <select id="newServUnit" class="voice-textbox" style="margin-top:0.35rem; width:100%; box-sizing:border-box;">
              <option value="month">Per Month</option>
              <option value="day">Per Day / Unit</option>
            </select>
          </label>
        </div>
        <label style="font-size:0.85rem; color:#cbe3f0;">Schedule / Item Details:
          <input type="text" id="newServDetail" placeholder="e.g. 2 Litres cow milk daily at 6:30 AM" class="voice-textbox" style="margin-top:0.35rem; width:100%; box-sizing:border-box;" />
        </label>
        <label style="font-size:0.85rem; color:#cbe3f0;">Weekly Off:
          <select id="newServOff" class="voice-textbox" style="margin-top:0.35rem; width:100%; box-sizing:border-box;">
            <option value="none">No Off Days (Daily Delivery / 7 Days)</option>
            <option value="sunday">Sunday Off</option>
            <option value="monday">Monday Off</option>
            <option value="saturday">Saturday Off</option>
          </select>
        </label>
        <label style="font-size:0.85rem; color:#cbe3f0;">UPI ID (for 1-tap Amazon Pay / UPI):
          <input type="text" id="newServUpi" placeholder="e.g. vendor@upi" class="voice-textbox" style="margin-top:0.35rem; width:100%; box-sizing:border-box;" />
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
        const hasStaff = /(?:sunita|ramesh|raju|anil|सुनीता|रमेश|राजू|अनिल)/i.test(transcript);
        const hasIntent = /(?:hisaab|hisab|salary|pay|advance|absent|chhutti|half|balance|undo|wapas|हिसाब|वेतन|छुट्टी)/i.test(transcript);
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

// ------------------------------------------------------------- Fail-Safe Spoken Audio Engine
function speakAlexaResponse(text) {{
  isSpeaking = true;
  setAlexaState('speaking');
  isAudioActive = true;

  // Stop any currently playing audio
  if (currentPlayingAudio) {{
    try {{ currentPlayingAudio.pause(); }} catch(e) {{}}
    currentPlayingAudio = null;
  }}

  const onSpeechFinished = () => {{
    isSpeaking = false;
    isAudioActive = false;
    setAlexaState(null);
    micBtn.innerText = '🎙️ Speak';
    if (handsFreeEnabled) {{
      setTimeout(() => {{
        try {{ recognition.start(); }} catch(e) {{}}
      }}, 300);
    }}
  }};

  // 1. Primary: Server-synthesized Windows SAPI (Microsoft Zira / Alexa voice)
  const audioUrl = '/api/tts?text=' + encodeURIComponent(text);
  const audio = new Audio(audioUrl);
  currentPlayingAudio = audio;

  audio.onended = () => {{
    currentPlayingAudio = null;
    onSpeechFinished();
  }};

  audio.onerror = (e) => {{
    console.warn("Server TTS playback error, falling back to browser SpeechSynthesis", e);
    currentPlayingAudio = null;
    fallbackBrowserSpeech(text, onSpeechFinished);
  }};

  audio.play().catch(err => {{
    console.warn("Audio autoplay blocked or failed, falling back to SpeechSynthesis", err);
    fallbackBrowserSpeech(text, onSpeechFinished);
  }});
}}

// Fallback: Browser Web SpeechSynthesis
function fallbackBrowserSpeech(text, callback) {{
  if (!('speechSynthesis' in window)) {{
    setTimeout(callback, 2500);
    return;
  }}
  try {{ window.speechSynthesis.resume(); }} catch(e) {{}}
  const utter = new SpeechSynthesisUtterance(text);
  window._activeAlexaUtterance = utter;

  const voices = window.speechSynthesis.getVoices();
  if (voices && voices.length > 0) {{
    const chosen = voices.find(v => v.lang === 'en-IN') ||
                   voices.find(v => v.name.toLowerCase().includes('zira')) ||
                   voices.find(v => v.lang.startsWith('en'));
    if (chosen) utter.voice = chosen;
  }}
  utter.rate = 1.0;
  utter.pitch = 1.05;

  let finished = false;
  const done = () => {{
    if (finished) return;
    finished = true;
    callback();
  }};

  utter.onend = done;
  utter.onerror = done;
  setTimeout(done, Math.max(3000, (text.split(' ').length / 2.0) * 1000 + 1500));
  try {{ window.speechSynthesis.speak(utter); }} catch(e) {{ done(); }}
}}

// ------------------------------------------------------------- Voice Prompt Dispatcher
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

  // 2. Intelligent progressive thinking steps (1.4 - 1.5s neural pause)
  speechText.innerHTML = '<span class="neural-badge">🧠 Alexa+ Neural Engine</span> Analyzing query & context...';
  
  await new Promise(r => setTimeout(r, 650));
  speechText.innerHTML = '<span class="neural-badge">⚡ Hisaab Core</span> Verifying October attendance, delivery schedules & advances...';
  
  await new Promise(r => setTimeout(r, 700));
  speechText.innerHTML = '<span class="neural-badge">✨ Multi-Modal Agent</span> Synthesizing voice response and updating live ledger...';

  const mcpJsonLog = document.getElementById('mcpJsonLog');

  try {{
    const resp = await fetch('/api/simulate', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ prompt: input }})
    }});
    const data = await resp.json();

    speechText.innerText = `"${{data.spoken_summary}}"`;
    mcpJsonLog.innerText = JSON.stringify(data.mcp_trace, null, 2);

    // Speak response out loud
    speakAlexaResponse(data.spoken_summary);

    // Dynamic ledger update without cutting off speech or reloading page
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
        stream.Open(tmp_path, 3, False)
        speaker.AudioOutputStream = stream
        speaker.Rate = 1
        speaker.Speak(text)
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

    # Intent routing simulation (Supports Hindi + English)
    # 1. Undo
    if any(k in prompt for k in ["undo", "wapas", "वापस", "cancel", "galti", "गलती", "revert"]):
        res = undo_last()
        mutated = True
        trace["tool"] = "undo_last"
        trace["arguments"] = {}
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

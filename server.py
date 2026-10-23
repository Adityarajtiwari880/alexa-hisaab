"""
Hisaab: A voice-first ledger for household help, served as an MCP server for Alexa+.
Production-ready build for the Amazon Developer Hackathon (Build, Ship, Shape).
Supports:
- Model Context Protocol (MCP) Streamable HTTP Transport (spec 2025-11-25+)
- Ultra-Modern 3D Holographic Alexa+ Voice & Visual Simulator
- 1-Tap Amazon Pay / UPI Settlement with Dynamic QR Code Generation
- Present-by-default pro-rata wage calculations & 8-week habit pattern recognition
- Multi-modal output: Spoken audio, HTML5 Cards, WhatsApp Slip, and iCal feed
"""

import calendar
import json
import os
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
        "concise and natural for voice synthesis. Always pass exact dates as YYYY-MM-DD."
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
    try:
        conn.execute("ALTER TABLE people ADD COLUMN upi_id TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


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
    if t in ("", "today", "now"):
        return now
    if t == "yesterday":
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
    if t in ("", "this month", "current", "now"):
        return now.year, now.month
    if t in ("last month", "previous", "previous month"):
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
    if t in ("none", "no", "never", ""):
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

    upi_id = person["upi_id"] if ("upi_id" in person.keys() and person["upi_id"]) else "sunita@apl"
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
        f"📋 Salary slip: {person['name']}, {month_label(m['year'], m['month'])}",
        f"Base Pay: {SYMBOL}{money(m['full_pay'])}",
    ]
    if m["deduction"]:
        lines.append(
            f"Absent: {m['absent_days'] + 0.5 * m['half_days']:g} day(s), "
            f"{m['paid_leaves']:g} paid leave. Deduction: {SYMBOL}{money(m['deduction'])}"
        )
    if m["bonus"]:
        lines.append(f"Bonus: {SYMBOL}{money(m['bonus'])}")
    if m["advances"]:
        lines.append(f"Advance taken: {SYMBOL}{money(m['advances'])}")
    if m["salary_paid"]:
        lines.append(f"Already paid: {SYMBOL}{money(m['salary_paid'])}")
    lines.append(f"Balance to pay: {SYMBOL}{money(m['balance'])}")
    if m["upi_id"]:
        lines.append(f"Amazon Pay / UPI Handle: {m['upi_id']}")
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
    st = {"absence": "absent", "halfday": "half_day", "half": "half_day", "came": "present"}.get(st, st)
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
  --panel-bg: rgba(13, 21, 29, 0.72);
  --panel-border: rgba(0, 210, 255, 0.18);
  --panel-highlight: rgba(255, 255, 255, 0.08);
  --text-main: #f0f6fa;
  --text-muted: #8ba2b0;
  --cyan-glow: #00d2ff;
  --alexa-blue: #0084ff;
  --neon-purple: #8a2be2;
  --coral-red: #ff5757;
  --emerald-green: #00e676;
  --gold-accent: #ffb703;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: var(--bg-dark);
  background-image: 
    radial-gradient(circle at 50% 0%, rgba(0, 132, 255, 0.14) 0%, transparent 60%),
    radial-gradient(circle at 10% 30%, rgba(0, 210, 255, 0.08) 0%, transparent 45%),
    radial-gradient(circle at 90% 70%, rgba(138, 43, 226, 0.08) 0%, transparent 50%);
  background-attachment: fixed;
  color: var(--text-main);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  min-height: 100vh;
}

main {
  max-width: 44rem;
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
  font-size: 2rem;
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

/* 3D Holographic Alexa Console */
.alexa-hero-console {
  position: relative;
  background: var(--panel-bg);
  backdrop-filter: blur(28px);
  -webkit-backdrop-filter: blur(28px);
  border: 1px solid var(--panel-border);
  border-radius: 1.5rem;
  padding: 2.25rem 1.75rem 1.75rem;
  margin-bottom: 2.5rem;
  box-shadow: 
    0 24px 60px rgba(0, 0, 0, 0.6),
    inset 0 1px 0 rgba(255, 255, 255, 0.12),
    0 0 35px rgba(0, 210, 255, 0.08);
  overflow: hidden;
}

/* Cyber Shimmer Light Bar */
.alexa-hero-console::before {
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, #00d2ff, #8a2be2, #00d2ff, transparent);
  background-size: 200% auto;
  animation: cyber-shimmer 4s linear infinite;
}
@keyframes cyber-shimmer {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}

/* 3D Orb Stage */
.orb-stage {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding-bottom: 1.25rem;
}
.orb-viewport {
  width: 140px;
  height: 140px;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  perspective: 900px;
}

/* The Core Glowing Sphere */
.orb-core {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  position: absolute;
  background: radial-gradient(circle at 35% 35%, #ffffff 0%, #00d2ff 35%, #0062ff 70%, #0b1528 100%);
  box-shadow: 
    0 0 25px rgba(0, 210, 255, 0.8),
    0 0 55px rgba(0, 132, 255, 0.4),
    inset -8px -8px 18px rgba(0, 0, 0, 0.85),
    inset 5px 5px 12px rgba(255, 255, 255, 0.7);
  animation: float-orb 4s ease-in-out infinite;
  transition: transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.4s;
}

/* 3D Gyroscope Ring 1 */
.orb-ring-1 {
  width: 110px;
  height: 110px;
  border-radius: 50%;
  position: absolute;
  border: 2px solid rgba(0, 210, 255, 0.65);
  box-shadow: 0 0 14px rgba(0, 210, 255, 0.45);
  transform-style: preserve-3d;
  animation: rotate-ring-1 6s linear infinite;
}
@keyframes rotate-ring-1 {
  0% { transform: rotateX(65deg) rotateY(0deg); }
  100% { transform: rotateX(65deg) rotateY(360deg); }
}

/* 3D Gyroscope Ring 2 */
.orb-ring-2 {
  width: 126px;
  height: 126px;
  border-radius: 50%;
  position: absolute;
  border: 1.5px dashed rgba(138, 43, 226, 0.6);
  box-shadow: 0 0 16px rgba(138, 43, 226, 0.35);
  transform-style: preserve-3d;
  animation: rotate-ring-2 9s linear infinite reverse;
}
@keyframes rotate-ring-2 {
  0% { transform: rotateX(115deg) rotateY(0deg); }
  100% { transform: rotateX(115deg) rotateY(360deg); }
}

@keyframes float-orb {
  0%, 100% { transform: translateY(0px) scale(1); }
  50% { transform: translateY(-7px) scale(1.02); }
}

/* Audio Waveform Canvas */
.wave-canvas {
  width: 220px;
  height: 38px;
  margin-top: 0.25rem;
  opacity: 0.85;
}

/* Voice Input Controls */
.input-dock {
  display: flex;
  gap: 0.6rem;
  margin-top: 1rem;
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
.action-btn-primary:active {
  transform: scale(0.97);
}

/* Quick Voice Trigger Capsules */
.trigger-section {
  margin-top: 1.25rem;
}
.trigger-header {
  font-size: 0.76rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
  margin-bottom: 0.6rem;
  font-weight: 700;
}
.trigger-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.trigger-pill {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.09);
  color: #cbe3eb;
  font-size: 0.83rem;
  font-weight: 500;
  padding: 0.42rem 0.85rem;
  border-radius: 9999px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  transition: all 0.2s ease;
  backdrop-filter: blur(8px);
}
.trigger-pill:hover {
  background: rgba(0, 210, 255, 0.12);
  border-color: rgba(0, 210, 255, 0.4);
  color: #fff;
  transform: translateY(-1px);
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
.inspector-tray summary:hover {
  color: var(--cyan-glow);
}
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
  background: linear-gradient(135deg, #00d2ff, #8a2be2);
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

            upi_drawer_html = f"""
            <div id="upi-drawer-{p_id}" class="upi-expansion-card" style="display:none;">
              <img class="upi-qr-image" src="{escape(m['qr_url'])}" alt="Amazon Pay / UPI QR Code" />
              <div class="upi-pay-info">
                <b>Pay {escape(p['name'])} via Amazon Pay / UPI</b><br/>
                <span class="upi-handle-tag">{escape(m['upi_id'])}</span><br/><br/>
                <span style="font-size:0.86rem; color:var(--text-muted)">Amount due: <b style="color:#fff">{SYMBOL}{money(m['balance'])}</b></span><br/><br/>
                <div style="display:flex; gap:0.5rem; flex-wrap:wrap">
                  <a class="action-btn-primary" href="{escape(m['upi_url'])}" style="text-decoration:none; padding:0.5rem 1rem;">Open UPI App</a>
                  <button class="trigger-pill" style="padding:0.5rem 1rem;" onclick="markSalaryPaid('{escape(p['name'])}')">✓ Mark as Paid</button>
                </div>
              </div>
            </div>
            """

            blocks.append(
                f'<div class="staff-card" id="card-{p_id}">'
                f'  <div class="card-header">'
                f'    <div class="card-profile">'
                f'      <div class="staff-avatar">{escape(initials)}</div>'
                f'      <div class="staff-names">'
                f'        <h2>{escape(p["name"])}</h2>'
                f'        <p>Active • Present by default ({WEEKDAYS[p["weekly_off"]].title() if p["weekly_off"] >= 0 else "No"} Off)</p>'
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
                f'  <div class="card-actions">'
                f'    <button class="btn-upi" onclick="toggleUpiDrawer({p_id})">💳 Pay with Amazon Pay / UPI</button>'
                f'    <a class="btn-whatsapp" href="{wa}" target="_blank" rel="noopener">💬 Send Slip on WhatsApp</a>'
                f'  </div>'
                f'  {upi_drawer_html}'
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

  <!-- 3D Holographic Alexa+ Console -->
  <section class="alexa-hero-console">
    <div class="orb-stage">
      <div class="orb-viewport" id="orbViewport">
        <div class="orb-ring-2" id="orbRing2"></div>
        <div class="orb-ring-1" id="orbRing1"></div>
        <div class="orb-core" id="orbCore"></div>
      </div>
      <canvas class="wave-canvas" id="waveCanvas" width="220" height="38"></canvas>
    </div>

    <!-- Voice Input Dock -->
    <div class="input-dock">
      <input type="text" id="voiceInput" class="voice-textbox" placeholder="Say or type: 'How much do I owe Sunita?'" />
      <button class="action-btn-primary" id="micBtn" onclick="toggleMic()">🎙️ Speak</button>
      <button class="action-btn-primary" onclick="sendVoicePrompt()">Ask</button>
    </div>

    <!-- Quick Test Prompts -->
    <div class="trigger-section">
      <div class="trigger-header">Quick Voice Triggers for Testing:</div>
      <div class="trigger-grid">
        <button class="trigger-pill" onclick="quickAsk('How much do I owe Sunita?')">🎙️ How much do I owe Sunita?</button>
        <button class="trigger-pill" onclick="quickAsk('Sunita took a half day today')">🎙️ Sunita took a half day today</button>
        <button class="trigger-pill" onclick="quickAsk('Give 2000 rupees advance to Sunita')">🎙️ Give 2000 rupees advance to Sunita</button>
        <button class="trigger-pill" onclick="quickAsk('Pay Sunita salary with Amazon Pay')">🎙️ Pay Sunita with Amazon Pay</button>
        <button class="trigger-pill" onclick="quickAsk('Undo last entry')">🎙️ Undo last entry</button>
      </div>
    </div>

    <!-- Alexa Spoken Speech Bubble -->
    <div class="hologram-bubble">
      <span class="bubble-speaker">Alexa</span>
      <span id="speechText">"Hisaab is active. Speak a command or tap a prompt above."</span>
    </div>

    <!-- Real-time MCP Inspector -->
    <details class="inspector-tray">
      <summary>⚡ MCP Streamable HTTP Protocol Inspector (JSON-RPC 2.0)</summary>
      <pre class="inspector-code" id="mcpJsonLog">// Streamable HTTP session active at /mcp
// Ready to trace JSON-RPC calls...</pre>
    </details>
  </section>

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
// ------------------------------------------------------------- 3D Audio Visualizer
const canvas = document.getElementById('waveCanvas');
const ctx = canvas.getContext('2d');
let waveStep = 0;
let isAudioActive = false;

function drawWave() {{
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const mid = canvas.height / 2;
  const amp = isAudioActive ? 12 : 3;

  ctx.beginPath();
  ctx.strokeStyle = isAudioActive ? '#00d2ff' : 'rgba(0, 210, 255, 0.35)';
  ctx.lineWidth = isAudioActive ? 2.5 : 1.5;

  for (let x = 0; x < canvas.width; x++) {{
    const y = mid + Math.sin((x * 0.05) + waveStep) * amp * Math.sin((x / canvas.width) * Math.PI);
    if (x === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }}
  ctx.stroke();

  waveStep += isAudioActive ? 0.18 : 0.04;
  requestAnimationFrame(drawWave);
}}
drawWave();

// ------------------------------------------------------------- Voice Recognition & Simulator
let recognizing = false;
let recognition = null;
const orbCore = document.getElementById('orbCore');

if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {{
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  recognition = new SpeechRec();
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.lang = 'en-IN';

  recognition.onstart = () => {{
    recognizing = true;
    isAudioActive = true;
    document.getElementById('micBtn').innerText = '🔴 Listening...';
    orbCore.style.boxShadow = '0 0 45px #00d2ff, 0 0 85px rgba(0, 210, 255, 0.8)';
    orbCore.style.transform = 'scale(1.15)';
  }};

  recognition.onresult = (e) => {{
    const text = e.results[0][0].transcript;
    document.getElementById('voiceInput').value = text;
    sendVoicePrompt(text);
  }};

  recognition.onend = () => {{
    recognizing = false;
    isAudioActive = false;
    document.getElementById('micBtn').innerText = '🎙️ Speak';
    orbCore.style.boxShadow = '0 0 25px rgba(0, 210, 255, 0.8), 0 0 55px rgba(0, 132, 255, 0.4)';
    orbCore.style.transform = 'scale(1)';
  }};
}}

function toggleMic() {{
  if (!recognition) {{
    alert("Speech recognition isn't supported in this browser. Please type into the box.");
    return;
  }}
  if (recognizing) recognition.stop();
  else recognition.start();
}}

function quickAsk(text) {{
  document.getElementById('voiceInput').value = text;
  sendVoicePrompt(text);
}}

async function sendVoicePrompt(customText) {{
  const input = customText || document.getElementById('voiceInput').value.trim();
  if (!input) return;

  const speechText = document.getElementById('speechText');
  const mcpJsonLog = document.getElementById('mcpJsonLog');
  speechText.innerText = "Processing via Alexa+ Agent Core over Streamable HTTP...";
  isAudioActive = true;

  try {{
    const resp = await fetch('/api/simulate', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ prompt: input }})
    }});
    const data = await resp.json();

    speechText.innerText = `"${{data.spoken_summary}}"`;
    mcpJsonLog.innerText = JSON.stringify(data.mcp_trace, null, 2);

    // Speak response via browser TTS
    if ('speechSynthesis' in window) {{
      window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(data.spoken_summary);
      utter.rate = 1.0;
      utter.pitch = 1.05;
      utter.onend = () => {{ isAudioActive = false; }};
      window.speechSynthesis.speak(utter);
    }} else {{
      isAudioActive = false;
    }}

    // Auto-reload on data mutation
    if (data.mutated) {{
      setTimeout(() => window.location.reload(), 1600);
    }}
  }} catch (err) {{
    speechText.innerText = "Connection error to Hisaab MCP server.";
    isAudioActive = false;
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
</script>
</body>
</html>"""
    return page_html


def token_ok(value: str) -> bool:
    return secrets.compare_digest(value.encode(), TOKEN.encode())


@mcp.custom_route("/api/simulate", methods=["POST"])
async def simulate_voice(request: Request) -> Response:
    """Simulates an Alexa+ conversational agent turn dispatching to FastMCP tools."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "spoken_summary": "Invalid JSON request."}, status_code=400)

    prompt = (body.get("prompt") or "").strip().lower()
    d = today()
    mutated = False
    trace = {
        "transport": "Streamable HTTP (/mcp)",
        "protocol_version": "2025-11-25",
        "method": "tools/call",
        "input_prompt": prompt,
    }

    # Intent routing simulation
    if "undo" in prompt:
        res = undo_last()
        mutated = True
        trace["tool"] = "undo_last"
        trace["arguments"] = {}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": mutated, "mcp_trace": trace})

    if "pay" in prompt and ("salary" in prompt or "amazon" in prompt or "upi" in prompt or "settle" in prompt):
        res = pay_salary(person="Sunita", payment_mode="amazon_pay")
        mutated = True
        trace["tool"] = "pay_salary"
        trace["arguments"] = {"person": "Sunita", "payment_mode": "amazon_pay"}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": mutated, "mcp_trace": trace})

    if "half day" in prompt:
        res = log_visit(person="Sunita", status="half_day")
        mutated = True
        trace["tool"] = "log_visit"
        trace["arguments"] = {"person": "Sunita", "status": "half_day"}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": mutated, "mcp_trace": trace})

    if "advance" in prompt:
        import re
        amt_match = re.search(r"(\d+)", prompt)
        amt = float(amt_match.group(1)) if amt_match else 2000.0
        res = log_payment(person="Sunita", amount=amt, kind="advance")
        mutated = True
        trace["tool"] = "log_payment"
        trace["arguments"] = {"person": "Sunita", "amount": amt, "kind": "advance"}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": mutated, "mcp_trace": trace})

    if "absent" in prompt:
        res = log_visit(person="Sunita", status="absent")
        mutated = True
        trace["tool"] = "log_visit"
        trace["arguments"] = {"person": "Sunita", "status": "absent"}
        trace["result"] = res
        return JSONResponse({"ok": True, "spoken_summary": res["spoken_summary"], "mutated": mutated, "mcp_trace": trace})

    # Default: month hisaab query
    res = month_hisaab(person="Sunita", month=f"{d.year}-{d.month:02d}")
    trace["tool"] = "month_hisaab"
    trace["arguments"] = {"person": "Sunita", "month": f"{d.year}-{d.month:02d}"}
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

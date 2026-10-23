"""
Hisaab: A voice-first ledger for household help, served as an MCP server for Alexa+.
Production-ready build for the Amazon Developer Hackathon (Build, Ship, Shape).
Supports:
- Model Context Protocol (MCP) Streamable HTTP Transport (spec 2025-11-25+)
- Alexa+ Echo Show Voice & Visual Simulator (with live Web Speech STT/TTS & MCP Inspector)
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
  --bg: #090e11;
  --cardbg: #111a1e;
  --ink: #e6edf0;
  --mute: #82959e;
  --rule: #1e2c33;
  --accent: #00d2ff;
  --accent-glow: rgba(0, 210, 255, 0.25);
  --alexa-blue: #00a8e8;
  --red: #ff6b6b;
  --green: #2ecc71;
  --btn: #00d2ff;
  --btnink: #071318;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
main { max-width: 42rem; margin: 0 auto; padding: 1.5rem 1rem 4rem; }

/* Echo Show / Alexa Simulator Header */
.simulator-box {
  background: linear-gradient(145deg, #101c23 0%, #0d161a 100%);
  border: 1px solid #1a3240;
  border-radius: 1.25rem;
  padding: 1.5rem;
  margin-bottom: 2rem;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.05);
  position: relative;
  overflow: hidden;
}
.simulator-box::before {
  content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, #00d2ff, #0077ff, #00d2ff);
  background-size: 200% auto;
  animation: shimmer 3s linear infinite;
}
@keyframes shimmer { 0% { background-position: 0% 0; } 100% { background-position: 200% 0; } }

.sim-header { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: 1rem; }
.sim-title { display: flex; align-items: center; gap: 0.75rem; font-weight: 700; font-size: 1.15rem; }
.alexa-ring {
  width: 22px; height: 22px; border-radius: 50%;
  border: 3px solid #00d2ff;
  box-shadow: 0 0 12px #00d2ff, inset 0 0 6px #00d2ff;
  animation: pulse-ring 2s infinite ease-in-out;
}
@keyframes pulse-ring {
  0%, 100% { transform: scale(1); opacity: 0.85; }
  50% { transform: scale(1.15); opacity: 1; box-shadow: 0 0 18px #00d2ff; }
}
.sim-badge {
  font-size: 0.75rem; padding: 0.25rem 0.65rem; border-radius: 2rem;
  background: rgba(0, 210, 255, 0.12); color: #00d2ff; border: 1px solid rgba(0, 210, 255, 0.3);
  font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
}

/* Voice Input & Quick Chips */
.voice-row { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
.voice-input {
  flex: 1; background: #080d10; border: 1px solid #1e2c33; border-radius: 0.5rem;
  padding: 0.65rem 0.85rem; color: #fff; font-size: 0.95rem;
}
.voice-input:focus { outline: none; border-color: #00d2ff; }
.mic-btn {
  background: #00d2ff; color: #071318; border: none; border-radius: 0.5rem;
  padding: 0 1rem; font-weight: 700; cursor: pointer; display: flex; align-items: center; gap: 0.4rem;
  transition: transform 0.15s, opacity 0.15s;
}
.mic-btn:hover { opacity: 0.9; }
.mic-btn:active { transform: scale(0.97); }

.chips-label { font-size: 0.8rem; color: var(--mute); margin-bottom: 0.4rem; text-transform: uppercase; letter-spacing: 0.04em; }
.chips-wrap { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.chip {
  background: #152228; border: 1px solid #233742; border-radius: 2rem;
  padding: 0.35rem 0.75rem; color: #d0dfe6; font-size: 0.82rem; cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.chip:hover { background: #1c303a; border-color: #00d2ff; color: #fff; }

/* Spoken Response Balloon */
.alexa-speech-box {
  background: #080d10; border-left: 3px solid #00d2ff; border-radius: 0 0.5rem 0.5rem 0;
  padding: 0.75rem 1rem; margin-top: 1rem; font-size: 0.95rem; line-height: 1.45;
  color: #cbe3eb; display: flex; align-items: flex-start; gap: 0.65rem;
}
.alexa-speech-box i { font-style: normal; color: #00d2ff; font-weight: 700; font-size: 0.8rem; text-transform: uppercase; }

/* Protocol Inspector */
details.inspector { margin-top: 0.75rem; font-size: 0.8rem; }
details.inspector summary { color: var(--mute); cursor: pointer; user-select: none; }
details.inspector pre {
  background: #05080a; padding: 0.6rem; border-radius: 0.4rem;
  color: #2ecc71; overflow-x: auto; font-family: ui-monospace, monospace; margin: 0.4rem 0 0;
}

/* Ledger Cards */
section.ledger-card {
  background: var(--cardbg); border: 1px solid var(--rule); border-radius: 1.15rem;
  padding: 1.5rem; margin-bottom: 1.5rem; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
}
h2 {
  font-size: 1.25rem; margin: 0 0 1rem; display: flex; justify-content: space-between;
  align-items: baseline; border-bottom: 1px solid var(--rule); padding-bottom: 0.75rem;
}
h2 span { font-size: 0.9rem; color: var(--mute); font-weight: 500; }
.row { display: flex; align-items: baseline; gap: 0.5rem; padding: 0.35rem 0; }
.row i { flex: 1; border-bottom: 1px dotted var(--rule); opacity: 0.6; transform: translateY(-0.25rem); }
.row b { font-variant-numeric: tabular-nums; font-weight: 600; }
.total {
  margin-top: 0.85rem; padding-top: 0.85rem; border-top: 2px solid var(--rule);
  font-size: 1.5rem; font-weight: 800;
}
.total .amt { color: var(--red); }
.note { color: var(--mute); font-size: 0.85rem; margin: 0.65rem 0 0; line-height: 1.4; }
.habit { color: #f1c40f; font-size: 0.88rem; margin-top: 0.6rem; font-weight: 500; }
.warn { color: var(--red); font-weight: 600; margin: 0.65rem 0 0; font-size: 0.88rem; }

/* Action Buttons & UPI Modal */
.actions { margin-top: 1.25rem; display: flex; gap: 0.65rem; flex-wrap: wrap; }
a.btn, button.btn {
  display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.65rem 1.1rem;
  border-radius: 0.5rem; background: var(--btn); color: var(--btnink);
  text-decoration: none; font-weight: 600; border: none; cursor: pointer; font-size: 0.9rem;
  transition: opacity 0.15s, transform 0.15s;
}
a.btn:hover, button.btn:hover { opacity: 0.9; }
a.btn:active, button.btn:active { transform: scale(0.97); }
a.btn.alt, button.btn.alt { background: #1a2830; color: #d0dfe6; border: 1px solid #233742; }
a.btn.wa { background: #25d366; color: #04200e; }
a.btn.upi { background: linear-gradient(135deg, #ff9900, #ffaa22); color: #111; font-weight: 700; }

.upi-drawer {
  margin-top: 1rem; padding: 1rem; background: #0a1114; border: 1px solid #1a2d37;
  border-radius: 0.75rem; display: flex; align-items: center; gap: 1.25rem; flex-wrap: wrap;
}
.upi-qr {
  width: 130px; height: 130px; border-radius: 0.5rem; background: #fff; padding: 5px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.4);
}
.upi-details { flex: 1; min-width: 160px; font-size: 0.88rem; }
.upi-details b { color: #fff; font-size: 1rem; }

footer { color: var(--mute); font-size: 0.88rem; border-top: 1px solid var(--rule); padding-top: 1.5rem; margin-top: 2rem; }
code { word-break: break-all; background: rgba(255,255,255,0.08); padding: 0.15rem 0.35rem; border-radius: 0.25rem; }
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
                f'<div class="row"><span>{escape(label)}</span><i></i><b>{"-" if v < 0 else ""}{SYMBOL}{money(abs(v))}</b></div>'
                for label, v in rows
            )
            extra = ""
            if m["advance_warning"]:
                extra += f'<p class="warn">Advances exceed safety limit of {SYMBOL}{money(m["advance_limit"])}.</p>'
            if m["absent_dates"]:
                days = ", ".join(date.fromisoformat(d).strftime("%d %b") for d in m["absent_dates"])
                extra += f'<p class="note">Absent on: {escape(days)}</p>'
            pat = pattern_note(conn, p)
            if pat:
                extra += f'<p class="habit">💡 Habit detected: {escape(pat)}</p>'

            wa = "https://wa.me/?text=" + urllib.parse.quote(slip_text(p, m))
            p_id = p["id"]

            upi_box = f"""
            <div id="upi-box-{p_id}" class="upi-drawer">
              <img class="upi-qr" src="{escape(m['qr_url'])}" alt="Amazon Pay / UPI QR Code" />
              <div class="upi-details">
                <b>Pay {escape(p['name'])} via Amazon Pay / UPI</b><br/>
                <span class="note">Amount: {SYMBOL}{money(m['balance'])} • Handle: <code>{escape(m['upi_id'])}</code></span><br/><br/>
                <a class="btn upi" href="{escape(m['upi_url'])}">Open UPI App</a>
                <button class="btn alt" onclick="recordUpiPayment('{escape(p['name'])}')">Mark as Paid</button>
              </div>
            </div>
            """

            blocks.append(
                f'<section class="ledger-card" id="card-{p_id}">'
                f'<h2>{escape(p["name"])}<span>{month_label(now.year, now.month)}</span></h2>'
                f'{lines}'
                f'<div class="total"><div class="row"><span>To pay</span><i></i>'
                f'<span class="amt">{SYMBOL}{money(m["balance"])}</span></div></div>'
                f'<p class="note">Counting {escape(p["name"])} present on every other working day.</p>'
                f'{extra}'
                f'<div class="actions">'
                f'<a class="btn wa" href="{wa}" target="_blank" rel="noopener">Send slip on WhatsApp</a>'
                f'<button class="btn upi" onclick="toggleUpi({p_id})">Pay with Amazon Pay / UPI</button>'
                f'</div>'
                f'{upi_box}'
                f'</section>'
            )

    if not blocks:
        blocks.append("<section class=\"ledger-card\"><p>No staff are set up yet. Use the Alexa+ console above to say: <em>set up Sunita at 9000 rupees a month</em>.</p></section>")

    page_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Hisaab — Alexa+ Household Ledger</title>
  <style>{PAGE_CSS}</style>
</head>
<body>
<main>
  <!-- Alexa+ Echo Show Voice Simulator -->
  <div class="simulator-box">
    <div class="sim-header">
      <div class="sim-title">
        <div class="alexa-ring" id="alexaRing"></div>
        <span>Alexa+ Echo Show Simulator</span>
      </div>
      <span class="sim-badge">Streamable HTTP /mcp</span>
    </div>

    <div class="voice-row">
      <input type="text" id="voiceInput" class="voice-input" placeholder="Say or type e.g., 'How much do I owe Sunita?'" />
      <button class="mic-btn" id="micBtn" onclick="toggleMic()">🎙️ Speak</button>
      <button class="btn" onclick="sendVoicePrompt()">Ask</button>
    </div>

    <div class="chips-label">Quick Voice Triggers for Testing:</div>
    <div class="chips-wrap">
      <button class="chip" onclick="quickAsk('How much do I owe Sunita?')">🎙️ How much do I owe Sunita?</button>
      <button class="chip" onclick="quickAsk('Sunita took a half day today')">🎙️ Sunita took a half day today</button>
      <button class="chip" onclick="quickAsk('Give 2000 rupees advance to Sunita')">🎙️ Give 2000 rupees advance to Sunita</button>
      <button class="chip" onclick="quickAsk('Pay Sunita salary with Amazon Pay')">🎙️ Pay Sunita with Amazon Pay</button>
      <button class="chip" onclick="quickAsk('Undo last entry')">🎙️ Undo last entry</button>
    </div>

    <div class="alexa-speech-box" id="speechBox">
      <i>Alexa</i>: <span id="speechText">"Hisaab is ready. Ask me about your staff attendance, salary balances, or advances."</span>
    </div>

    <details class="inspector" id="inspectorBox">
      <summary>🔍 MCP Protocol Inspector (Streamable HTTP JSON-RPC)</summary>
      <pre id="mcpJsonLog">Ready to inspect MCP requests...</pre>
    </details>
  </div>

  <h1>Hisaab</h1>
  <p class="note" style="margin-bottom:1.5rem">Voice-first household ledger as of {now.strftime("%d %B %Y")}.</p>

  <div id="ledgerCardsContainer">
    {"".join(blocks)}
  </div>

  <footer>
    <p>Paydays, absences and balances sync with your calendar automatically.</p>
    <div class="actions">
      <a class="btn alt" href="{gcal}" target="_blank" rel="noopener">Add to Google Calendar</a>
    </div>
    <p class="note">Calendar feed URL: <code>{escape(cal_url)}</code></p>
    <p class="note">MCP Streamable HTTP Endpoint: <code>{escape(b_url)}/mcp</code></p>
  </footer>
</main>

<script>
let recognizing = false;
let recognition = null;

if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {{
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  recognition = new SpeechRec();
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.lang = 'en-IN';

  recognition.onstart = () => {{
    recognizing = true;
    document.getElementById('micBtn').innerText = '🔴 Listening...';
    document.getElementById('alexaRing').style.boxShadow = '0 0 25px #00d2ff';
  }};

  recognition.onresult = (e) => {{
    const transcript = e.results[0][0].transcript;
    document.getElementById('voiceInput').value = transcript;
    sendVoicePrompt(transcript);
  }};

  recognition.onend = () => {{
    recognizing = false;
    document.getElementById('micBtn').innerText = '🎙️ Speak';
    document.getElementById('alexaRing').style.boxShadow = '0 0 12px #00d2ff';
  }};
}}

function toggleMic() {{
  if (!recognition) {{
    alert("Speech recognition isn't supported in this browser. Please type in the input box.");
    return;
  }}
  if (recognizing) {{
    recognition.stop();
  }} else {{
    recognition.start();
  }}
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
  speechText.innerText = "Processing via Alexa+ & FastMCP...";

  try {{
    const resp = await fetch('/api/simulate', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ prompt: input }})
    }});
    const data = await resp.json();

    speechText.innerText = `"${{data.spoken_summary}}"`;
    mcpJsonLog.innerText = JSON.stringify(data.mcp_trace, null, 2);

    // Speak aloud using Web Speech Synthesis
    if ('speechSynthesis' in window) {{
      window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(data.spoken_summary);
      utter.rate = 1.0;
      utter.pitch = 1.05;
      window.speechSynthesis.speak(utter);
    }}

    // Auto-reload to reflect database state changes
    if (data.mutated) {{
      setTimeout(() => window.location.reload(), 1400);
    }}
  }} catch (err) {{
    speechText.innerText = "Connection error to Hisaab MCP server.";
  }}
}}

function toggleUpi(id) {{
  const el = document.getElementById('upi-box-' + id);
  if (el) {{
    el.style.display = (el.style.display === 'none' || !el.style.display) ? 'flex' : 'none';
  }}
}}

function recordUpiPayment(person) {{
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

# Hisaab — Voice-First Household Ledger & Amazon Pay Hub for Alexa+

> **Amazon Developer Hackathon — Alexa+ Track Submission**  
> Built with open standards: **Agent Skills (`SKILL.md`)**, **Model Context Protocol (MCP)** over **Streamable HTTP Transport** (`spec 2025-11-25+`), and **FastMCP**.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/MCP-Streamable%20HTTP%20(2025--11--25%2B)-blue.svg)](https://modelcontextprotocol.io)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-Open%20Standard-purple.svg)](SKILL.md)
[![Python](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://python.org)
[![Tests](https://img.shields.io/badge/pytest-12%20passed-brightgreen.svg)](tests/test_server.py)

---

## 📌 Executive Summary

**Hisaab** is an ambient, voice-first household micro-economy operating system and financial ledger built natively as an Agent Skill and self-hosted MCP server for **Alexa+**.

In millions of households across emerging economies, managing domestic help (maids, cooks, drivers, dhobis, milkmen) and recurring fixed obligations (rent, EMIs, utility bills) is plagued by forgotten absences, verbal cash advances, disputed paid leaves, and awkward month-end negotiations.

Instead of cumbersome spreadsheets or mobile apps that demand daily manual entries, **Hisaab** reimagines the experience:
1. **Present by Default**: Staff are assumed present unless an exception occurs. You only speak when someone is absent or takes an advance.
2. **Zero-Friction Voice Logging**: *"Alexa, tell Hisaab the maid took a half day yesterday."*
3. **The Dhobi Piece-Rate Tab**: *"Alexa, gave 12 clothes to the dhobi today"* — automatically tracks per-piece ironing tabs at ₹5/cloth.
4. **Transparent Damage & Penalty Deductions**: *"Alexa, deduct ₹50 from Ramesh for the broken glass"* — cleanly itemized on WhatsApp slips to avoid resentment.
5. **Flatmate / PG Bill Splitter**: *"Alexa, split the maid's ₹4,000 bill with Amit"* — generates multi-tenant shares, individual UPI deep links, and 1-tap WhatsApp payment requests.
6. **Annual Insights & Macro Spend Engine**: *"Alexa, what's my household summary for the year?"* — narrating total spend in Lakhs, peak vacation months, and attendance champions.
7. **Amazon Pay Fixed Overheads & AutoPay Hub**: Tracks fixed EMIs, rent, and utility bills with simulated Amazon Pay AutoPay e-mandates, due date countdowns, and 1-tap settlement.
8. **Total Household Monthly Burn & Liquidity Advisory**: *"Alexa, what is my total household burn this month?"* — answers the homeowner's ultimate question and advises minimum bank liquidity buffers before AutoPay dates.
9. **Dignified Worker Transparency**: Instant bilingual (English + Hindi) itemized WhatsApp slips with scannable QR codes for seamless UPI settlement.
10. **Authentic Echo 3D Bezel Console**: High-fidelity 3D Echo Crown console with procedural Web Audio wake chimes, progressive neural thought stream, and hands-free wake word detection (*"Alexa..."*).

---

## 🏗️ Architecture

```
                 ┌────────────────────────────────────────────────────────┐
                 │                  Alexa+ Voice / Host                   │
                 └───────────────────────────┬────────────────────────────┘
                                             │
                               Streamable HTTP (POST/GET /mcp)
                               MCP Spec 2025-11-25+ Standard
                                             │
                 ┌───────────────────────────▼────────────────────────────┐
                 │                Hisaab FastMCP Server                   │
                 │                     (server.py)                        │
                 └───────────┬───────────────────────────┬────────────────┘
                             │                           │
            ┌────────────────▼───┐       ┌───────────────▼──────────────────────────────┐
            │   SQLite Ledger    │       │            Starlette HTTP Server             │
            │    (hisaab.db)     │       ├──────────────────────────────────────────────┤
            │                    │       │ • / (Interactive Alexa+ Echo Show Simulator) │
            │ • people (staff)   │       │ • /mcp (Streamable HTTP Endpoint)            │
            │ • events (history) │       │ • /api/simulate (Conversational Agent Turn)  │
            │ • commitments(EMIs)│       │ • /api/tts (Windows SAPI / Browser TTS)      │
            │ • upi_id (payment) │       │ • /h/{token}/calendar.ics (RFC 5545 iCal)    │
            └────────────────────┘       │ • Dynamic Amazon Pay / UPI QR & Deep Links   │
                                         │ • 1-Click WhatsApp Salary Slip Dispatch      │
                                         └──────────────────────────────────────────────┘
```

---

## 🛠️ MCP Tools & Agent Skills Exposed to Alexa+

Hisaab exposes **19 FastMCP tools** to the Alexa+ agentic runtime:

| # | Tool Name | Key Arguments | Voice Trigger Example |
| :-: | :--- | :--- | :--- |
| **1** | `set_up_person` | `name`, `pay_amount`, `pay_unit`, `weekly_off`, `upi_id` | *"Alexa, set up Sunita at 9000 rupees a month with Sunday off."* |
| **2** | `log_visit` | `person`, `status` (`absent`, `half_day`, `present`), `day` | *"Alexa, Sunita took a half day today."* |
| **3** | `log_absences` | `person`, `dates` (`list[str]`), `note` | *"Alexa, record Sunita absent on October 5th, 12th, and 19th."* |
| **4** | `give_advance` | `person`, `amount`, `day` | *"Alexa, give 2000 rupees advance to Sunita."* |
| **5** | `record_payment` | `person`, `amount`, `kind` (`bonus`, `payment`), `day` | *"Alexa, gave 500 rupees bonus to Ramesh."* |
| **6** | `pay_salary` | `person`, `amount`, `payment_mode` (`amazon_pay`, `upi`) | *"Alexa, pay Sunita's salary with Amazon Pay."* |
| **7** | `month_hisaab` | `person`, `month` | *"Alexa, how much do I owe Sunita this month?"* |
| **8** | `household_hisaab` | `month` | *"Alexa, what is my total household help balance for October?"* |
| **9** | `list_people` | None | *"Alexa, list my household staff."* |
| **10** | `undo_last` | None | *"Alexa, undo last Hisaab entry."* |
| **11** | `annual_insights` | `year` | *"Alexa, what's my household summary for the year?"* |
| **12** | `log_pieces` | `person`, `count`, `rate_per_piece` | *"Alexa, gave 12 clothes to the dhobi today."* |
| **13** | `log_penalty` | `person`, `amount`, `reason` | *"Alexa, deduct 50 rupees from Ramesh for the broken glass."* |
| **14** | `split_bill` | `person`, `split_with`, `amount` | *"Alexa, split the maid's 4,000 bill with Amit."* |
| **15** | `list_commitments` | `month` | *"Alexa, list my fixed bills and EMIs."* |
| **16** | `add_commitment` | `name`, `amount`, `due_day`, `category`, `autopay` | *"Alexa, add iPhone 16 Pro EMI of 4200 due on the 5th."* |
| **17** | `pay_commitment_amazon_pay` | `name`, `month` | *"Alexa, pay iPhone EMI on Amazon Pay."* |
| **18** | `toggle_autopay` | `name`, `enable` | *"Alexa, toggle AutoPay for iPhone EMI on Amazon Pay."* |
| **19** | `total_household_burn` | `month` | *"Alexa, what is my total household burn this month?"* |
| **20** | `upcoming_reminders` | `days_ahead` | *"Alexa, any EMIs due this week?"* |

---

## 🚀 Quickstart & Setup

### 1. Prerequisites
- Python 3.10+
- Modern Web Browser (Chrome / Edge recommended for Web Speech recognition)

### 2. Installation
```bash
git clone https://github.com/adityarajtiwari/alexa-hisaab.git
cd alexa-hisaab
pip install -r requirements.txt
```

### 3. Running the Server & Simulator
```bash
python server.py
```
Open **`http://localhost:8000/`** in your browser.

- **Interactive Echo Show Simulator**: `http://localhost:8000/`
- **Streamable HTTP MCP Endpoint**: `http://localhost:8000/mcp`
- **Live Visual Ledger**: `http://localhost:8000/h/demo`
- **iCalendar Feed**: `http://localhost:8000/h/demo/calendar.ics`

### 4. Running the Automated Test Suite
```bash
python -m pytest tests/test_server.py
```
**12/12 unit tests passing in <1s**.

---

## 🧪 Testing the MCP Streamable HTTP Transport

Hisaab strictly adheres to the **MCP Streamable HTTP** transport standard (`spec 2025-11-25+`).

### 1. Initialize MCP Session:
```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "alexa-client", "version": "1.0.0"}}}'
```

### 2. Call `total_household_burn` Tool:
```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "total_household_burn", "arguments": {"month": "2026-10"}}}'
```

---

## 📜 License
Distributed under the [MIT License](LICENSE).

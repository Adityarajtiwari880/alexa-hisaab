# Hisaab — Voice-First Household Ledger for Alexa+

> **Amazon Developer Hackathon (Build, Ship, Shape) — Alexa+ Track Submission**  
> Built with open standards: **Agent Skills (`SKILL.md`)**, **Model Context Protocol (MCP)** using **Streamable HTTP Transport** (`spec 2025-11-25+`), and **FastMCP**.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/MCP-Streamable%20HTTP%20(2025--11--25%2B)-blue.svg)](https://modelcontextprotocol.io)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-Open%20Standard-purple.svg)](SKILL.md)
[![Python](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://python.org)

---

## 📌 Executive Summary

**Hisaab** is a voice-first household staff ledger built natively as an Agent Skill and self-hosted MCP server for **Alexa+**. In households across emerging economies (such as India), millions employ domestic help (cooks, maids, drivers, nannies) on informal monthly or daily wages. Bookkeeping is plagued by forgotten absences, verbal cash advances, disputes over paid leaves, and awkward month-end negotiations.

Instead of cumbersome manual spreadsheets or mobile apps that demand daily attention, **Hisaab** flips the paradigm:
1. **Present by Default**: Staff are assumed present unless an exception occurs. You only speak when someone is absent or takes an advance.
2. **Zero-Friction Voice Logging**: *"Alexa, tell Hisaab the maid took a half day yesterday."*
3. **Context-Aware Math & Guardrails**: Pro-rata daily wage deductions, paid leave offsets, and 50% advance overdraft warnings.
4. **Behavioral Habit Recognition**: Analyzes an 8-week sliding window and flags recurring absence habits (*"Sunita was absent on 3 of the last 8 Mondays"*).
5. **1-Tap Amazon Pay / UPI Settlement**: Instant generation of dynamic UPI deep links and scannable QR codes for zero-friction digital payout.
6. **Multi-Modal Echo Show Simulator**: Interactive web console with bidirectional Web Speech STT/TTS, interactive ledger cards, WhatsApp salary slips, and live iCalendar sync.

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
            │ • people (rates)   │       │ • /mcp (Streamable HTTP Endpoint)            │
            │ • events (history) │       │ • /h/{token}/calendar.ics (RFC 5545 iCal)    │
            │ • upi_id (payment) │       │ • Dynamic Amazon Pay / UPI QR & Deep Links   │
            └────────────────────┘       │ • 1-Click WhatsApp Salary Slip Dispatch      │
                                         └──────────────────────────────────────────────┘
```

---

## 🛠️ MCP Tools & Agent Skills Exposed to Alexa+

| Tool | Parameters | Spoken Voice Trigger Example |
| :--- | :--- | :--- |
| `set_up_person` | `name`, `pay_amount`, `pay_unit`, `weekly_off`, `paid_leaves_per_month`, `pay_day`, `upi_id` | *"Alexa, set up Sunita at 9000 rupees a month with Sunday off."* |
| `log_visit` | `person`, `status` (`absent`, `half_day`, `late`, `present`), `day`, `note` | *"Alexa, tell Hisaab the maid took a half day yesterday."* |
| `log_absences` | `person`, `dates` (`list[str]`), `note` | *"Alexa, record Sunita absent on October 5th, 12th, and 19th."* |
| `log_payment` | `person`, `amount`, `kind` (`advance`, `salary`, `bonus`), `day`, `note` | *"Alexa, give 2000 rupees advance to Sunita."* |
| `pay_salary` | `person`, `amount`, `payment_mode` (`amazon_pay`, `upi`), `note` | *"Alexa, pay Sunita's salary with Amazon Pay."* |
| `month_hisaab` | `person`, `month` | *"Alexa, how much do I owe Sunita this month?"* |
| `household_hisaab` | `month` | *"Alexa, what is my total household help balance for October?"* |
| `list_people` | None | *"Alexa, list my household staff."* |
| `undo_last` | None | *"Alexa, undo last Hisaab entry."* |

---

## 🚀 Quickstart & Setup

### 1. Prerequisites
- Python 3.10+
- Virtual environment recommended

### 2. Installation
```bash
git clone https://github.com/adityarajtiwari/hisaab-alexa-mcp.git
cd hisaab-alexa-mcp
pip install -r requirements.txt
```

### 3. Running the Server & Simulator
```bash
python server.py
```
Open **`http://localhost:8000/`** in your browser.

- **Interactive Alexa+ Echo Show Simulator**: `http://localhost:8000/`
- **MCP Endpoint**: `http://localhost:8000/mcp` (Streamable HTTP)
- **Live Visual Ledger**: `http://localhost:8000/h/demo`
- **iCalendar Feed**: `http://localhost:8000/h/demo/calendar.ics`

### 4. Running Unit Tests
```bash
python -m pytest
```

---

## 🧪 Testing the MCP Streamable HTTP Transport

Hisaab strictly adheres to the **MCP Streamable HTTP** transport standard.

### Initialize MCP Session:
```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "alexa-client", "version": "1.0.0"}}}'
```

### Call `month_hisaab` Tool:
```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "month_hisaab", "arguments": {"person": "Sunita", "month": "2026-10"}}}'
```

---

## 📱 Interactive Features & Multi-Modal Output

1. **Echo Show Voice Simulator**: Real-time microphone listening via Web Speech API + speech synthesis TTS for Alexa voice responses.
2. **MCP Protocol Inspector**: Collapsible terminal tray in the web console displaying real-time JSON-RPC 2.0 frames.
3. **1-Tap Amazon Pay / UPI**: Generates dynamic UPI links (`upi://pay?pa=sunita@apl&...`) and scannable QR codes.
4. **WhatsApp Payslip**: Pre-fills an itemized salary slip sent directly via WhatsApp (`https://wa.me/?text=...`).
5. **Google / Apple Calendar Feed**: Auto-updating `.ics` calendar subscription with dynamic balance updates.

---

## 📜 License
Distributed under the [MIT License](LICENSE).

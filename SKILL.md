---
name: hisaab-household-ledger
description: Voice-first domestic staff attendance, pro-rata salary ledger, and UPI/Amazon Pay settlement agent for Alexa+
version: 1.2.0
author: Aditya Raj Tiwari
transport: streamable-http
mcp_spec: 2025-11-25
---

# Hisaab — Voice-First Household Ledger Agent Skill

## Overview
Hisaab is an Agent Skill and MCP server designed for **Alexa+** to handle household staff bookkeeping (maids, cooks, drivers, milkmen, and nannies). 

Unlike conventional accounting applications that require active daily attendance logging, Hisaab operates on a **Present-by-Default** paradigm. Domestic staff are automatically presumed present unless the user verbally reports an exception (absence, half-day, or advance payment).

## Trigger Phrases
- "Alexa, how much do I owe Sunita?"
- "Alexa, Sunita took a half day today."
- "Alexa, record 2,000 rupees advance for maid."
- "Alexa, pay Sunita's salary with Amazon Pay."
- "Alexa, set up Ramesh (Cook) at 6,000 rupees a month with Sunday off."
- "Alexa, what is my total household help balance for this month?"
- "Alexa, what's my household summary for the year?"
- "Alexa, undo last entry."

## Architecture & Transport
- **MCP Spec**: `2025-11-25+`
- **Transport**: `Streamable HTTP` at endpoint `/mcp`
- **Media Support**: Echo Show Cards, SVG/Dynamic UPI QR Codes, WhatsApp Salary Slips, and RFC 5545 `.ics` Live Calendar Feeds.
- **Database**: Local SQLite with ACID transactional event sourcing and rollback support.

## Available Tools

### 1. `set_up_person`
Registers a new household staff member with their contractual rate and policies.
- `name`: Staff name (e.g., "Sunita", "Ramesh").
- `pay_amount`: Salary amount in standard currency.
- `pay_unit`: `month` or `day`.
- `weekly_off`: Designated weekly off-day (e.g., `sunday`, `monday`, or `none`).
- `paid_leaves_per_month`: Allowed monthly paid leave quota (default: 1.0).
- `pay_day`: Day of month for salary settlement (1–28).
- `upi_id`: Optional Amazon Pay / UPI handle (e.g. `sunita@apl`).

### 2. `log_visit`
Records an attendance exception.
- `person`: Name of the staff member.
- `status`: `absent`, `half_day`, `late`, or `present` (used to correct an earlier accidental absence).
- `day`: ISO date `YYYY-MM-DD`, `today`, or `yesterday`.
- `note`: Optional descriptive note.

### 3. `log_absences`
Batch logs multiple absent dates at once (ideal for month-end reconciliation).
- `person`: Name of the staff member.
- `dates`: Array of ISO date strings `["2026-10-05", "2026-10-12"]`.

### 4. `log_payment`
Records financial transactions outside of regular salary payout.
- `person`: Name of the staff member.
- `amount`: Transaction amount.
- `kind`: `advance`, `salary`, or `bonus`.
- `day`: Transaction date.

### 5. `pay_salary`
Settles the monthly balance via UPI / Amazon Pay.
- `person`: Staff name.
- `amount`: Settlement amount (defaults to entire current pro-rated balance).
- `payment_mode`: `upi`, `amazon_pay`, or `cash`.

### 6. `month_hisaab`
Retrieves comprehensive pro-rata breakdown for an individual staff member, including deductions, paid leave offsets, advance warnings, 8-week habit pattern insights, and UPI QR links.
- `person`: Name of the staff member.
- `month`: Target month formatted as `YYYY-MM` or `this month`.

### 7. `household_hisaab`
Aggregates expenditure across all active household staff.
- `month`: Target month `YYYY-MM`.

### 8. `list_people`
Enumerates all active staff members and their pay parameters.

### 9. `undo_last`
Reverts the most recent event transaction to correct verbal errors.

### 10. `annual_insights`
Provides macro-level yearly household analytics and spending intelligence across all domestic staff, daily milk supplies, and subscriptions.
- `year`: Optional calendar year (defaults to current year, e.g., 2026).
- **Spoken Summary**: Natural narrative for Alexa+ voice synthesis (*"You spent 1.2 Lakhs on household help this year. Sunita took the most leave in May, and Ramesh had the best attendance."*).
- **Structured JSON**: Returns full monthly spending trend, service-by-service breakdown, attendance champion, peak leave month, and delivery units for LLM reasoning.

## Multi-Modal Output & Integrations
- **Echo Show / Web Card**: Displays itemized ledger lines, pro-rated deductions, and live balance.
- **Dynamic UPI QR Code**: Instant deep link `upi://pay?pa={upi_id}&pn={name}&am={balance}&tn=Salary` for 1-tap mobile payment.
- **WhatsApp Webhook / Share**: Generates a pre-formatted itemized salary slip sent directly to staff via WhatsApp.
- **Calendar Feed**: Live iCal subscription (`/h/{token}/calendar.ics`) synchronizing paydays and dynamic balances with Google Calendar or Apple Calendar.

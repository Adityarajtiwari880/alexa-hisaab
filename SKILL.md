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
- "Alexa, any EMIs due this week?"
- "Alexa, pay iPhone EMI on Amazon Pay."
- "Alexa, what is my total household burn this month?"
- "Alexa, enable AutoPay for iPhone EMI on Amazon Pay."
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

### 11. `log_pieces`
Records piece-rate services such as Dhobi ironing or tailoring. Multiplies the piece count by the helper's unit rate (e.g., ₹5/piece) and appends directly to their monthly ledger tab.
- `person`: Helper name (e.g., "Ramlal Dhobi").
- `count`: Number of clothes / pieces.
- `rate_per_piece`: Optional custom rate override.

### 12. `log_penalty`
Records transparent damage or penalty deductions (e.g. broken glassware, damaged property). Deducts directly from final gross salary and itemizes clearly on the WhatsApp salary slip.
- `person`: Staff name (e.g., "Ramesh").
- `amount`: Token deduction amount (e.g., ₹50).
- `reason`: Description of damage / reason (e.g., "broken glass").

### 13. `split_bill`
Splits household bills (maid, cook, Wi-Fi) among flatmates or PG roommates. Produces equal shares, separate UPI deep links, and pre-formatted WhatsApp payment requests.
- `person`: Staff or service name (e.g., "Sunita").
- `split_with`: Names of flatmates (e.g., "Amit", "Rahul").
- `amount`: Optional bill amount override (defaults to current balance).

### 14. `list_commitments`
Lists all recurring household fixed commitments, loan EMIs, rent, and utility bills with due dates, amounts, and Amazon Pay AutoPay e-mandate status.
- `month`: Optional month string `YYYY-MM`.

### 15. `add_commitment`
Registers a new recurring fixed obligation (e.g. iPhone EMI, Flat Rent, Electricity) with Amazon Pay AutoPay settings.
- `name`: Commitment title (e.g., "iPhone 16 Pro EMI").
- `amount`: Monthly obligation amount (e.g., 4200.0).
- `due_day`: Day of month due (1–31).
- `category`: `emi`, `rent`, `utility_bill`, or `subscription`.
- `autopay`: Boolean flag to activate Amazon Pay AutoPay e-mandate.
- `provider`: AutoPay service provider (defaults to "Amazon Pay").

### 16. `pay_commitment_amazon_pay`
Initiates an instant 1-tap Amazon Pay payment for a fixed obligation. Generates UPI deep links, Amazon Pay native intent links, dynamic QR code, and updates payment receipt.
- `name`: Commitment title or keyword (e.g. "iPhone", "Tata Power", "Rent").

### 17. `toggle_autopay`
Enables or disables Amazon Pay AutoPay e-mandates for an EMI or bill.
- `name`: Commitment title.
- `enable`: Optional boolean to explicitly enable or disable.

### 18. `total_household_burn`
Calculates comprehensive monthly household burn: combines variable domestic staff salaries, daily deliveries (milk, newspaper, water), piece-work (dhobi), and fixed commitments (EMIs, rent, bills). Also forecasts upcoming 7-day liquidity requirements to prevent AutoPay bounces.
- `month`: Target month `YYYY-MM`.

### 19. `upcoming_reminders`
Scans for all EMIs, utility bills, and salary settlement dues in the next N days. Highlights whether payment is automated via Amazon Pay AutoPay or requires manual clearance.
- `days_ahead`: Lookahead window in days (default: 7).

## Multi-Modal Output & Integrations
- **Echo Show / Web Card**: Displays itemized ledger lines, pro-rated deductions, and live balance.
- **Amazon Pay AutoPay Badge**: Visual status indicator (🟢 AutoPay Active / ⚠️ Manual Pay) with instant toggle.
- **Dynamic UPI & Amazon Pay QR Code**: Instant deep link `upi://pay?pa=amazonpay@apl&pn=...` and `amazonpay://pay?...` for 1-tap mobile payment.
- **WhatsApp Webhook / Share**: Generates a pre-formatted itemized salary slip sent directly to staff via WhatsApp.
- **Calendar Feed**: Live iCal subscription (`/h/{token}/calendar.ics`) synchronizing paydays and dynamic balances with Google Calendar or Apple Calendar.

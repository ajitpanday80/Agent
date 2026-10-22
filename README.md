# MSP L1 Voice Agent

LiveKit Agents + Deepgram (STT/TTS) + OpenRouter (LLM), with a mock PSA (SQLite), a sourced L1 knowledge base
and a rules-based priority/escalation engine.

## Setup
    python3 -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env     # fill in keys. Never commit .env

## Try it without a phone number
    python scripts/text_chat.py                      # chat in text (real LLM + tools, no mic needed)
    python -m msp_agent.agent console                # voice via your terminal mic (run on your own machine)
    python -m msp_agent.agent dev                    # voice from browser: https://agents-playground.livekit.io
    python -m pytest tests                           # rules + ticketing tests

Seed callers (so identify_caller can recognize them): Priya Shah (VIP) and Tom Becker at Acme Dental;
Maria Lopez and James Okoro at Northwind Logistics. Anyone else is treated as an unrecognized caller.

## Inspect the mock PSA
    python -m msp_agent.cli list [--status open]
    python -m msp_agent.cli show T-1001
    python -m msp_agent.cli note T-1001 "called user back"
    python -m msp_agent.cli status T-1001 in_progress --assignee alice
    python -m msp_agent.cli incident-add "Print server down" printing     # new tickets in that category link to it
    python -m msp_agent.cli escalations
    python -m msp_agent.cli seed-demo                                    # 4 example tickets, LOCAL ONLY (add --zoho to also push them to Zoho Desk)
    python -m msp_agent.cli create --name "Tom Becker" --company "Acme Dental" --category email --summary "Outlook down" --severity stopped
Data lives in `data/msp.db` (gitignored; override with `MSP_DB`).

## Zoho Desk is the system of record
With the `ZOHO_*` values in `.env`, tickets live in Zoho Desk (otherwise a local SQLite mock is used):
- **Open early:** every call gets a ticket as soon as the issue, scope and severity are known. Subject format:
  `[P3] [email] summary` (priority and category are parsed from it to detect repeat calls).
- **Close if fixed:** when a knowledge-base step fixes it and the caller confirms, the agent closes the ticket
  (comment with the fix, status Closed). Never for security, access, or escalated tickets.
- **Hang-up safety net:** if the call ends first, an open ticket gets a "call ended" note. If the caller gave a
  name and number but no ticket exists, a low-priority "Call ended before the issue was captured" ticket is created.
- **Callers:** recognized only if a Zoho contact matches BOTH name and company (company comes from the contact's
  Account). Unrecognized callers share one placeholder contact (`Unverified Caller`) so strangers never become
  "known" contacts. Add your clients as Contacts with an Account in Zoho. VIPs: set `MSP_VIP_EMAILS=a@x.com,b@y.com`.
- **Repeat calls:** merged into the caller's open ticket of the same category (recognized callers only).
- **Zoho down:** the ticket is saved locally with `sync_error=pending...`; push later with
  `python -m msp_agent.cli retry-sync`.
- **Local files:** `data/msp.db` keeps only the call log (`cli calls`, `cli escalations`), known incidents, and any
  tickets saved while Zoho was down.
- Read-only connection check: `python scripts/zoho_check.py`.
- Lookups list and filter client-side because the token lacks `Desk.search.READ`. Fine for a pilot-sized directory;
  add that scope to the Self Client for larger ones (not yet implemented).
- Tests never touch Zoho (`tests/conftest.py` blocks it).

## How it decides (important)
The LLM only reports **facts** (category, scope, severity, security/human/hostile flags). `escalation.py` decides
priority and action, so behavior is testable and not left to the model.
- Priority: impact x urgency matrix (P1 to P4), then overrides.
- Security incident or data loss: always P1, immediate human. Caller is told to disconnect from the network but
  not power off (CISA guidance).
- Password / lockout / MFA: never done by the AI. Always a ticket for technician callback with identity verification.
- VIP caller and repeat callers (3rd contact on the same open issue): bumped one level.
- Printing and performance issues are capped (never P1 unless security).
- Human requested or caller hostile: transfer in business hours, else callback next business day.
- P1/P2: transfer in business hours, page on-call after hours.
- Unrecognized caller: no ticket or account details shared.
Config: `MSP_TZ` (default UTC), `MSP_HOURS` (default 08:00-18:00, Mon-Fri), `LIVE_TRANSFER_ENABLED` (default false).

## Caveats
- SLA numbers in `escalation.py` are placeholder examples, not an ITIL standard. Use your contract terms.
- KB content in `kb.py` is paraphrased from the cited Microsoft and CISA pages; review before production.
  The MFA article is based on community Q&A; verify against your tenant's Entra documentation.
- "Recognized" caller means a name/company match, **not** verified identity. Caller ID can be spoofed.
- Live transfer, real SIP/phone, and real PSA integration are not built yet.

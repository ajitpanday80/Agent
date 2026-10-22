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
    python -m msp_agent.cli seed-demo                                    # 4 example tickets
    python -m msp_agent.cli create --name "Tom Becker" --company "Acme Dental" --category email --summary "Outlook down" --severity stopped
Data lives in `data/msp.db` (gitignored; override with `MSP_DB`).

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

# RelayDesk

RelayDesk is a Flask admin console and LiveKit voice agent for MSP service operations. Zoho Desk is the only ticket system of record. SQLite stores the editable knowledge library, incidents, handoff/voicemail intake, routing settings, VIP routing flags, and audit events; it does not create or mirror tickets.

## Setup

1. Create a Zoho Desk OAuth Self Client and grant the scopes needed for tickets, contacts, agents, and departments. Set the Zoho organization and default ticket department.
2. Create a Python environment and install dependencies:

       python3 -m venv .venv
       . .venv/bin/activate
       pip install -r requirements.txt
       cp .env.example .env

3. Set the Zoho OAuth values plus a long `HELPDESK_ADMIN_PASSWORD` and persistent random `HELPDESK_SESSION_SECRET` in `.env`. Use the API and Accounts domains for the Zoho data center where the organization lives. Do not commit `.env`.
4. Start the admin console:

       python -m msp_agent.web

   Open <http://127.0.0.1:5000> and sign in with `HELPDESK_ADMIN_USER` (default `admin`) and the configured password. For deployment, run `gunicorn --bind 127.0.0.1:8000 msp_agent.web:app --workers 2` behind HTTPS, set `HELPDESK_COOKIE_SECURE=true`, and keep the service on a private network.

## Admin console

- **Ticket queue:** list and create tickets in Zoho Desk, open ticket conversations, update status, assign Zoho agents, and add private comments. Tickets created by the voice agent or dashboard are assigned to an active agent in the configured department with the fewest open, on-hold, or escalated tickets.
- **Customers & agents:** view Zoho accounts and contacts, create accounts/contacts, see support agents, and set local VIP priority flags for contacts.
- **Knowledge base:** manage sourced troubleshooting articles and safe steps, globally or scoped to an exact Zoho account. The agent uses local BM25 RAG, only includes client-specific articles after an exact recognized contact match, and passes retrieved steps and source URLs into the LLM prompt. Imported text is chunked and stays draft until review. License basis, attribution, and upstream source are tracked before publication. See [reusable KB sources and review steps](docs/KB_SOURCES.md). This does not require an embedding service.
- **Voicemail & handoffs:** track staff follow-up, caller requested transfers, and caller-authorized voicemail transcripts alongside Zoho tickets.
- **Known incidents, routing settings, and audit log:** maintain issue notices, business hours/time zone, after-hours routing preference, and a record of dashboard changes.

The Markdown articles in `kb/` are sourced drafts for staff review; they are not automatically activated. Review them against the client environment, then import approved content through the dashboard's Knowledge Base page.

This checkout auto-pushes each Git commit to its same-named branch on `origin`. On a fresh clone, enable the tracked hook with `git config core.hooksPath .githooks`. File edits are pushed after they are committed.

## Voice agent

Set `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `DEEPGRAM_API_KEY`, and `OPENROUTER_API_KEY`. Then run:

    python -m msp_agent.agent console

The agent only reads issue facts, retrieves approved KB guidance, and submits tickets to Zoho Desk. The escalation rules compute priority and staff action. If Zoho is unavailable, a ticket is not represented as created; the call request is saved in the staff intake queue for recovery. Ticket details and modifications are withheld from callers until trusted identity verification is available.

For a single-command local setup, run `python scripts/dev.py` from the project root after installing dependencies and configuring `.env`. This starts both the Flask dashboard and LiveKit agent in development mode; open the LiveKit Cloud Agent Console separately to test the browser session. Press Ctrl+C to stop both processes.

### Connect a LiveKit Phone Number

The agent registers with the dispatch name in `LIVEKIT_AGENT_NAME` (default `msp-helpdesk`) and explicitly connects to each assigned room. In LiveKit Cloud, create an individual dispatch rule named `MSP Helpdesk Inbound`, add `msp-helpdesk` under the rule's agent/room configuration, save it, then open **Telephony → Phone Numbers** and assign that rule to your purchased number. A LiveKit-managed number does not need a separate inbound trunk. To test the number against your local worker using the default production dispatch, run `python scripts/dev.py --agent-mode start`; for a cloud deployment, deploy the agent to production instead. The default `dev` mode is for browser Agent Console testing. Test by calling the number from another phone.

## Human transfer and voicemail

In the admin dashboard, open **Routing & system** and set **Human agent destination** and **Voicemail destination** using E.164 numbers (for example `+61255550123`) or SIP URIs. For urgent P1/P2 calls outside the coverage policy, select **Voicemail transfer destination** if those calls should be forwarded; select **Staff follow-up queue** to leave them for staff instead. A caller asking for a human is transferred to the human destination regardless of business hours. A caller asking to leave voicemail is routed to the voicemail destination.

Transfers use LiveKit's `TransferSIPParticipant` SIP REFER operation. The account's API credentials must permit SIP call control, and the LiveKit/provider SIP trunk must accept REFER and allow transfers to the configured destination. Confirm provider support and place real test calls before relying on this route. If a transfer fails, the caller stays with the agent; the request remains in Zoho Desk and the staff handoff queue. For voicemail fallback, the agent can capture a caller-authorized speech transcript and add it as a private Zoho comment. This does not create an audio recording; real voicemail audio is handled by the configured voicemail system.

## Ticket API and local data

Zoho Desk OAuth credentials are required for all ticket operations. The existing `msp_agent.cli` is a small Zoho-backed helper; the Flask console is the staff interface. `MSP_DB` can point the operational SQLite data to a different file. Legacy local ticket tables from older database files are not read by the current service.

Zoho Desk API supports read/write ticket operations and regional API domains; use the domain returned for your Zoho data center and the corresponding OAuth region. See the [official Zoho Desk API documentation](https://desk.zoho.com/DeskAPIDocument).

## Operational limits

- Current ticket and account/contact lists load the first 50 Zoho records; add pagination for larger queues.
- SLA targets in `escalation.py` are example values. Set them to your customer contracts before use.
- Knowledge articles are starter content. Review every article and source against your client environments before enabling caller guidance.
- Contact matching uses exact name, phone, and Zoho account matches; this is not caller identity verification. Trusted caller-ID verification needs the phone provider.
- Configure Zoho custom fields, departments, technician roles, and business policies in Zoho Desk to match your MSP.

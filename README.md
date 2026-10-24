# RelayDesk

RelayDesk is a web dashboard and LiveKit voice agent for an MSP helpdesk. **Zoho Desk is the system of record for tickets.** The voice agent answers inbound calls, checks caller records, searches the reviewed troubleshooting library, creates/updates Zoho tickets, and handles configured transfers and voicemail fallback. SQLite stores the KB, incidents, routing settings, handoffs, VIP flags, and audit log.

## What you need

- Git and Python **3.11** (recommended for the LiveKit/Silero dependency stack; Python 3.10–3.14 may also work with compatible wheels).
- A Zoho Desk organization with an OAuth Self Client and a support department.
- A LiveKit Cloud project. A purchased LiveKit phone number is optional for browser testing and required for phone testing.
- API credentials for Deepgram (speech recognition) and OpenRouter (the language model).
- A microphone and a modern browser for local browser testing.

## 1. Get the project from GitHub

Clone the repository and enter the project directory:

```bash
git clone https://github.com/ajitpanday80/Agent.git
cd Agent
```

If you are using the development branch rather than the default branch, check it out after cloning:

```bash
git fetch origin
git switch claude/adoring-bardeen-x10grt
```

This repository has a post-commit hook that pushes commits to the same-named branch on `origin`. Enable it once per clone:

```bash
git config core.hooksPath .githooks
```

GitHub only receives committed changes. To share a change, commit it; the hook pushes it. Open or update the pull request if the branch needs to be merged into the default branch.

## 2. Create a Python environment

### macOS

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

### Linux (Ubuntu/Debian)

First, ensure Python 3.11 is installed:
```bash
sudo apt update
sudo apt install python3.11 python3.11-venv python3.11-dev
```

Then create the environment:
```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

### Windows

**Using PowerShell (Recommended):**

First, download and install Python 3.11 from [python.org](https://www.python.org/downloads/windows/). Make sure to check "Add Python to PATH" during installation.

Then open PowerShell and run:
```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

**If you get an execution policy error** when activating the virtual environment:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Then try activating again:
```powershell
.venv\Scripts\Activate.ps1
```

**Using Command Prompt (cmd):**
```cmd
py -3.11 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
```

**Important Notes:**
- Keep the virtual environment active for all commands below (you'll see `(.venv)` in your terminal prompt)
- The project uses `python-dotenv`; install the full `requirements.txt` rather than installing individual packages to avoid missing dependencies
- On Windows, you may need to install [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) if you encounter compilation errors during `pip install`

## 3. Configure Zoho Desk

1. Sign in to the Zoho API Console for the same Zoho data center as your Desk organization and create a **Self Client**.
2. Generate an authorization code for the required Desk scopes. The application needs ticket read/write, contact/account read/create, agent read, and department read access. Zoho scope names include `Desk.tickets.ALL`, `Desk.contacts.READ`, `Desk.contacts.CREATE`, `Desk.agents.READ`, `Desk.departments.READ`, and `Desk.basic.READ`; grant only the scopes allowed by your portal and add a narrower missing scope if Zoho returns a permission error.
3. Exchange the authorization code for an access token and refresh token using the matching regional Zoho Accounts OAuth endpoint. Save the **refresh token**, client ID, and client secret. The application refreshes short-lived access tokens automatically.
4. Find your Zoho Desk organization ID and the ID of the department that receives support tickets.
5. In `.env`, set the correct regional values and credentials:

   - `ZOHO_ACCOUNTS_URL` — for example `https://accounts.zoho.in` for an India data center.
   - `ZOHO_DESK_API_URL` — for example `https://desk.zoho.in/api/v1`.
   - `ZOHO_DESK_ORG_ID`, `ZOHO_DESK_DEPARTMENT_ID`, `ZOHO_DESK_WEB_URL`.
   - `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN`.

   Use your own data-center domains. Do not copy the example India domains if your Desk account is hosted elsewhere.
6. Verify the connection without printing secrets:

   ```bash
   python scripts/zoho_check.py
   ```

   The script should return successful organization and department responses. If it reports a scope error, grant the specific read/write scope in the Self Client and generate a new refresh token.

Zoho’s [Self Client setup guide](https://www.zoho.com/developer/oauth/self-client/overview.html) explains creating the client and obtaining tokens. The [Zoho Desk API guide](https://desk.zoho.com/DeskAPIDocument) explains regional endpoints, OAuth scopes, and API pagination.

## 4. Configure LiveKit and voice services

1. Create or open your LiveKit Cloud project. Copy its WebSocket URL, API key, and API secret into `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`.
2. Set `LIVEKIT_AGENT_NAME=msp-helpdesk` unless your LiveKit dispatch rule uses another name.
3. Create API credentials with Deepgram and OpenRouter. Set `DEEPGRAM_API_KEY` and `OPENROUTER_API_KEY`. The default model is configured by `OPENROUTER_MODEL`; change it only to a model supported by your OpenRouter account.
4. For local browser testing, you do not need to configure a phone number or dispatch rule yet.
5. For phone calls, create an **individual inbound dispatch rule** in LiveKit named for the agent (`msp-helpdesk` by default), then assign that rule to your purchased number in **Telephony → Phone Numbers**. Confirm the number is active and the rule targets the same agent name.

## 5. Secure the admin login

Open `.env` and set:

```dotenv
HELPDESK_ADMIN_USER=admin
HELPDESK_ADMIN_PASSWORD=use-a-long-unique-password
HELPDESK_SESSION_SECRET=use-a-long-random-secret
```

Generate a session secret on macOS/Linux with `openssl rand -hex 32`. Set your own password; there is no shared default password. For local development, keep `HELPDESK_HOST=127.0.0.1`, `HELPDESK_PORT=5000`, and `HELPDESK_COOKIE_SECURE=false`. Never expose the development server directly to the public internet.

**Never commit `.env`.** It contains API credentials and is excluded from Git. The repository tracks `data/msp.db`, which is shared with the repository when committed; it may contain contacts, handoff messages, configuration, and audit information. Push it only to an access-controlled repository and avoid placing caller-sensitive data in a public repository.

## 6. Start the dashboard and agent

From the project root, with `.venv` active and `.env` configured, run both services with one command:

### Quick Start (Default: Browser Testing Mode)

**macOS/Linux:**
```bash
python scripts/dev.py
```

**Windows:**
```powershell
python scripts/dev.py
```

Or use the full path on Windows if needed:
```powershell
.\.venv\Scripts\python.exe scripts\dev.py
```

This starts both the dashboard and LiveKit agent in **browser testing mode** (dev mode). Open <http://127.0.0.1:5000> and sign in with the username and password from `.env`.

For browser testing:
1. Keep the terminal open (press **Ctrl+C** to stop both services)
2. Open the LiveKit Cloud **Agent Console / Playground**
3. Connect it to your project
4. Speak through your browser microphone to test the agent

### Production Mode (Phone Calls)

To receive real phone calls through a configured LiveKit dispatch rule, you have two options:

**Option 1: Command-line override**
```bash
python scripts/dev.py --agent-mode start
```

**Option 2: Set in .env file**

Edit your `.env` file and change:
```dotenv
LIVEKIT_AGENT_MODE=start
```

Then run:
```bash
python scripts/dev.py
```

With production mode active, call the LiveKit number from another phone to test. Do not run multiple copies of the worker against the same dispatch rule while testing.

**Note:** If one service exits with an error, the combined command stops and prints that service's logs. Zoho must be configured for ticket creation; if the voice provider credentials are missing, the agent will fail to start.

## 7. Configure human transfer and voicemail

In the dashboard, open **Routing & system** and enter the human-agent and voicemail destinations in E.164 format (for example `+61255550123`) or as SIP URIs. Save the settings. Then place test calls for both routes.

Call forwarding uses LiveKit SIP REFER. The phone trunk must support REFER and permit transfers to each destination. If the transfer fails, RelayDesk records the handoff and follows its configured fallback. Caller message transcription is saved only after the caller agrees; it is not an audio recording. Check the handoff in **Voicemail & handoffs** and the linked Zoho ticket.

## 8. Use the admin dashboard

- **Ticket queue:** browse Zoho tickets, open ticket detail, change status/assignee, add private notes, and create a Zoho ticket. The dashboard loads 25 tickets per page; its text filter searches the current page.
- **Customers & agents:** browse Zoho accounts and contacts with separate page controls. VIP flags are stored in RelayDesk. When adding a contact or client-specific KB article, enter the exact Zoho account name.
- **Knowledge base:** search and preview retrieval, edit articles, import source material as drafts, and approve safe steps. The starter library contains a small set of enabled shared runbooks; the rest remain drafts until reviewed. The voice agent retrieves enabled articles with local BM25 ranking (RAG); it does not train a model on the content or require an embedding service. See [KB source and review guidance](docs/KB_SOURCES.md) and [review checklist](docs/KB_REVIEW.md).
- **Known incidents:** add and resolve current service notices. The voice agent checks these before troubleshooting.
- **Voicemail & handoffs:** review caller requests, transfer outcomes, callbacks, and consented message transcripts.
- **Routing & system:** set time zone, 24/7 or weekday coverage, escalation destination, SLA reference targets, and transfer destinations. Configure contractual SLA enforcement separately in Zoho Desk.
- **Audit log:** review administrative changes.

Ticket, account, contact, KB, handoff, incident, audit, and ticket-comment lists are paginated. Zoho Desk listing requests use the `from` and `limit` API parameters; RelayDesk displays 25 records per page. The Zoho API allows up to 50 per request for most listings (some endpoints allow more).

## 9. Understand caller handling

1. The agent checks inbound caller ID against Zoho contacts/accounts and uses matching names/company details for the greeting. Caller ID is not identity verification.
2. The agent asks what is happening, scope/impact, workaround, and possible security concerns. It checks active known incidents.
3. For a relevant approved KB article, it offers one step at a time. It asks whether the device is Windows or macOS when needed. If no relevant article matches, it creates or escalates a ticket instead of inventing a fix.
4. Tickets are created in Zoho Desk and assigned using the active agent with the lowest open workload when assignment data is available. Zoho remains authoritative for tickets and conversations.
5. Password, account lockout, MFA, suspected compromise, and other high-risk cases go to a technician. The agent must not reset credentials or bypass verification.
6. Caller requests for a person or voicemail follow the saved routing settings. The dashboard records failed transfers and approved follow-up actions.

## 10. Run in a server environment

Do not use Flask’s development server for a public production service. Run behind HTTPS on a private network, use a process manager, and keep `.env` in the server’s secret store. Example Gunicorn command:

```bash
gunicorn --bind 127.0.0.1:8000 msp_agent.web:app --workers 2
```

Run the LiveKit agent worker as a separate managed process using the production dispatch mode, configure `HELPDESK_COOKIE_SECURE=true` behind HTTPS, use a strong session secret, and monitor logs and process health. Back up `data/msp.db` securely; it contains operational records and settings. Configure customer-specific policies, Zoho SLA rules, support hours, phone transfer targets, and escalation contacts before using the service for customer calls.

## Common problems

- **`ModuleNotFoundError: dotenv`** — activate `.venv` and run `pip install -r requirements.txt` from the project root.
- **Dependency resolution conflict** — use Python 3.11, recreate the virtual environment, upgrade pip, and install the full requirements file.
- **Zoho is not configured / 401 / 403** — check the six `ZOHO_*` values, correct regional domains, organization ID, refresh-token validity, and granted scopes; rerun `python scripts/zoho_check.py`.
- **No LiveKit worker appears** — verify `LIVEKIT_URL`, API key/secret, agent name, and that the terminal reports successful worker registration. Browser testing uses dev mode; a purchased number uses production dispatch mode and an assigned dispatch rule.
- **Calls do not transfer** — verify E.164/SIP destinations, API credentials, trunk REFER support, and destination permission. Test with a real call.
- **No caller troubleshooting answer** — the issue must match an enabled KB article. Check the article status and use the KB preview/search in the dashboard.
- **"80% of concurrent adaptive interruption requests" warning from LiveKit** — The interruption detection settings have been optimized to reduce API usage. The agent now requires 1.2 seconds of speech and at least 3 words before treating input as an interruption, preventing false triggers from background noise. If you still see high usage, consider reducing call volume or upgrading your LiveKit plan.

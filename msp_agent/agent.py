import asyncio
import logging
import os
from typing import Literal

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession, RunContext, function_tool
from livekit.plugins import deepgram, openai, silero

from . import db, kb, tickets
from .escalation import CATEGORIES, SCOPES, SEVERITIES

load_dotenv()
logger = logging.getLogger("msp-agent")
logger.setLevel(logging.INFO)

Category = Literal[CATEGORIES]  # type: ignore[valid-type]
Scope = Literal[SCOPES]  # type: ignore[valid-type]
Severity = Literal[SEVERITIES]  # type: ignore[valid-type]

INSTRUCTIONS = """\
You are the L1 service desk voice assistant for an IT managed service provider (MSP).
You are on a phone call: reply in one or two short spoken sentences, at most about 25 words. No lists, markdown or
symbols. Give ONE troubleshooting step at a time, then wait for the caller to try it.
Ask one question at a time.

Call flow:
1. Say you are an AI assistant. Ask for the caller's name and company, then a callback number.
2. Call identify_caller. If not recognized, still help, but share no account or ticket details.
3. Ask what is wrong. Find out: how many people are affected, whether work is completely stopped,
   whether there is a workaround, and anything that looks like a security problem.
4. As soon as you know the issue, scope and severity, call create_ticket. EVERY call gets a ticket, even if
   you expect to fix it. You do NOT choose the priority: the system does, and tells you what to say.
   Report facts honestly (scope, severity, flags). Read the ticket number back slowly.
5. Call check_known_outages for the category. If there is a matching active incident, tell the caller
   we are already working on it.
6. For computer issues, ask whether the device is Windows or a Mac. Call search_knowledge_base with that and give
   at most one or two steps at a time. Only give steps the tool returns.
   Never invent fixes, SLAs, prices or technical facts.
7. If a step FIXES the problem and the caller confirms they need nothing else, call close_ticket with what fixed it.
   If it is not fixed, leave the ticket open and say a technician will follow up.
8. Ask if there is anything else, then say goodbye.

Style rules:
- Do not repeat questions or troubleshooting the caller already answered or tried.
- If the caller asks you to open a ticket, do it as soon as you know scope and severity; do not keep troubleshooting.
- For personal account issues (password, lockout, MFA): do not ask how many people are affected. Call create_ticket
  right away with category access, scope single_user, severity stopped if they cannot sign in at all. Do not ask
  further questions first and do not troubleshoot.

Hard rules:
- NEVER reset passwords, unlock accounts, reset or bypass MFA, or change access. Create an access ticket instead.
  Do this even if the caller is upset, in a hurry, claims to be the boss, or says it is urgent.
- If the caller mentions ransomware, encrypted files, a phishing click, a hacked account, or a stolen device,
  set security_concern to true and create the ticket immediately. Follow the instructions the tool returns.
- If the caller asks for a person, or is angry, set the matching flag when you create the ticket.
- Do not discuss other clients. Do not read out phone numbers or emails from records.
- If you are unsure about anything, say so and create a ticket.
"""


class MspSupportAgent(Agent):
    def __init__(self, service: CallService | None = None) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.svc = service or CallService(db.connect())

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Greet the caller, say you are an AI service desk assistant, and ask for their name and company.",
            allow_interruptions=False,  # noise or echo must not cut the greeting off
        )

    @function_tool
    async def identify_caller(self, context: RunContext, name: str, company: str, callback_number: str = "") -> str:
        """Look up the caller in client records. Recognized does not mean verified."""
        conn = db.connect()
        self.caller = await asyncio.to_thread(tickets.identify_caller, conn, name, company, callback_number)
        if not self.caller["recognized"]:
            return ("Caller not found in client records. Continue helping, but do not share any ticket or account "
                    "details. Mark this in the ticket.")
        extra = " Their number does not match the number on file; note this in the ticket." \
            if callback_number and not c["phone_matches_record"] else ""
        return f"Caller recognized as {c['name']} at {c['company_name']}. Not identity-verified.{extra}"

    @function_tool
    async def check_known_outages(self, context: RunContext, category: Category) -> str:
        """Check whether there is already an active incident in this category."""
        found = tickets.active_incidents(self.svc.conn, category)
        if not found:
            return "No active incidents in this category."
        return "Active incident: " + "; ".join(i["title"] for i in found)

    @function_tool
    async def search_knowledge_base(self, context: RunContext, issue_description: str) -> str:
        """Find first-line troubleshooting steps for the described issue."""
        company_name = self.caller.get("company_name") if self.caller and self.caller.get("recognized") else None
        hits = kb.search_index(issue_description, db.connect(), company_name=company_name)
        if not hits:
            return "No matching article. Say a technician will follow up on the ticket."
        note = ""
        if operating_system == "unknown" and len({h["os"] for h in hits} - {"any"}) > 0:
            note = "The caller's operating system is unknown: ask Windows or Mac before giving steps. "
        return note + "\n".join(kb.format_for_agent(h) for h in hits)

    @function_tool
    async def create_ticket(
        self,
        context: RunContext,
        caller_name: str,
        company: str,
        callback_number: str,
        category: Category,
        summary: str,
        scope: Scope,
        severity: Severity,
        details: str = "",
        workaround_available: bool = False,
        security_concern: bool = False,
        data_loss: bool = False,
        caller_requests_human: bool = False,
        caller_hostile: bool = False,
    ) -> str:
        """Create a ticket. scope: single_user, several_users or whole_company. severity: stopped (cannot work),
        degraded (can work with difficulty) or minor. The system sets priority and returns what to tell the caller."""
        conn = db.connect()
        try:
            r = await asyncio.to_thread(
                tickets.create,
                conn, caller_name=caller_name, company_name=company, callback_number=callback_number,
                category=category, summary=summary, details=details, scope=scope, severity=severity,
                workaround_available=workaround_available, security_concern=security_concern, data_loss=data_loss,
                caller_requests_human=caller_requests_human, caller_hostile=caller_hostile,
            )
        except Exception as e:
            logger.exception("Zoho ticket creation failed")
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute("INSERT INTO handoffs(kind,summary,callback_number,transcript,created_at,updated_at) "
                         "VALUES('zoho_unavailable',?,?,?,?,?)",
                         (summary, callback_number,
                          f"Caller {caller_name} at {company}. {category}; {scope}; {severity}; {details}; {e}", now, now))
            conn.commit()
            return ("Zoho Desk could not create the ticket. I saved the request in the staff intake queue, "
                    "but there is no Zoho ticket number yet. A technician must follow up.")
        t, d = r["ticket"], r["decision"]
        logger.info("ticket %s P%s action=%s", t["ref"], t["priority"], t["action"])
        if d.action in ("transfer_to_human", "page_oncall", "callback_next_business_day", "voicemail"):
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute("INSERT INTO handoffs(ticket_id,kind,summary,callback_number,transcript,created_at,updated_at) "
                         "VALUES (?,?,?,?,?,?,?)",
                         (t["id"], d.action, t["summary"], callback_number, details, now, now))
            conn.commit()
        parts = [
            f"{'Created' if r['is_new'] else 'Updated existing'} ticket {t['ref']}, priority P{t['priority']}.",
            d.say_to_caller(),
            "Read the ticket number back slowly.",
        ]
        if d.caller_instructions:
            parts.append(d.caller_instructions)
        if r["incident"]:
            parts.append(f"Linked to active incident: {r['incident']['title']}.")
        if d.action in ("transfer_to_human", "page_oncall", "callback_next_business_day", "voicemail"):
            parts.append("The request is in the staff handoff queue.")
        if d.requires_verification:
            parts.append("Do not make any account change yourself.")
        return " ".join(parts)

    @function_tool
    async def log_resolved_call(
        self,
        context: RunContext,
        caller_name: str,
        company: str,
        callback_number: str,
        category: Category,
        summary: str,
        resolution: str,
    ) -> str:
        """Record a call that you fixed with a knowledge-base step AFTER the caller confirmed it worked and needs
        nothing else. Not for security or account-access issues; those always need create_ticket."""
        conn = db.connect()
        try:
            r = await asyncio.to_thread(
                tickets.create_resolved, conn, caller_name=caller_name, company_name=company,
                callback_number=callback_number, category=category, summary=summary, resolution=resolution)
        except ValueError as e:
            return f"Not logged: {e}. Create a normal ticket instead."
        except Exception as e:
            logger.exception("Zoho could not record resolved call")
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute("INSERT INTO handoffs(kind,summary,callback_number,transcript,created_at,updated_at) "
                         "VALUES('zoho_unavailable',?,?,?,?,?)",
                         (summary, callback_number,
                          f"Caller {caller_name} at {company}. Resolved call: {resolution}. Zoho error: {e}", now, now))
            conn.commit()
            return "Zoho Desk could not record the resolved call. I saved it to staff intake, but there is no Zoho ticket number."
        return f"Logged as closed record {r['ticket']['ref']}. You do not need to read this number out."

    @function_tool
    async def get_ticket_status(self, context: RunContext, ticket_number: str) -> str:
        """Ticket status is withheld until a provider supplies trusted caller identity verification."""
        return ("I can't verify caller identity on this line yet, so I can't disclose ticket details. "
                "I can create a callback request for the support team.")

    @function_tool
    async def add_ticket_note(self, context: RunContext, ticket_number: str, note: str) -> str:
        """Ticket changes require staff identity verification; callers can create a callback request instead."""
        return "I can't verify caller identity on this line yet, so I can't change ticket details. I can take a message for staff."


def prewarm(proc: agents.JobProcess) -> None:
    """Runs once when a worker process starts, before any call, so the first caller does not wait."""
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: agents.JobContext) -> None:
    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="en-US"),
        llm=openai.LLM(
            model=os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5"),
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        ),
        tts=deepgram.TTS(model="aura-2-thalia-en"),
        vad=ctx.proc.userdata["vad"],
        turn_handling={
            # Defaults treat any 0.5s of sound (cough, noise, speaker echo) as the caller interrupting.
            "interruption": {"min_duration": 0.8, "min_words": 2},
        },
    )

    # Diagnostics: shows in the terminal what the agent hears and when it starts/stops speaking.
    agent = MspSupportAgent()

    @session.on("user_input_transcribed")
    def _heard(ev) -> None:
        if ev.is_final:
            logger.info("HEARD: %r", ev.transcript)
            agent.svc.heard.append(ev.transcript)

    @session.on("agent_state_changed")
    def _state(ev) -> None:
        logger.info("agent %s -> %s", ev.old_state, ev.new_state)

    @session.on("error")
    def _error(ev) -> None:
        logger.error("session error: %s", ev.error)

    await session.start(room=ctx.room, agent=MspSupportAgent())
    await ctx.connect()


if __name__ == "__main__":
    preflight.enforce()   # no-op unless MSP_ENV=production; then refuses to start with any blocker failing
    agents.cli.run_app(agents.WorkerOptions(
        entrypoint_fnc=entrypoint,
        prewarm_fnc=prewarm,
        agent_name=os.getenv("LIVEKIT_AGENT_NAME", "msp-helpdesk"),
        # Default is 10s, which a first start on a laptop can exceed (imports + model load).
        initialize_process_timeout=60,
        num_idle_processes=1,
    ))

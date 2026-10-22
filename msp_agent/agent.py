import asyncio
import logging
import os
from typing import Literal

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession, RunContext, function_tool
from livekit.plugins import deepgram, openai, silero

from . import db, kb, tickets, zoho
from .escalation import CATEGORIES, SCOPES, SEVERITIES

load_dotenv()
logger = logging.getLogger("msp-agent")

Category = Literal[CATEGORIES]  # type: ignore[valid-type]
Scope = Literal[SCOPES]  # type: ignore[valid-type]
Severity = Literal[SEVERITIES]  # type: ignore[valid-type]

INSTRUCTIONS = """\
You are the L1 service desk voice assistant for an IT managed service provider (MSP).
You are on a phone call: reply in one or two short spoken sentences. No lists, markdown or symbols.
Ask one question at a time.

Call flow:
1. Say you are an AI assistant. Ask for the caller's name and company, then a callback number.
2. Call identify_caller. If not recognized, still help, but share no account or ticket details.
3. Ask what is wrong. Find out: how many people are affected, whether work is completely stopped,
   whether there is a workaround, and anything that looks like a security problem.
4. Call check_known_outages for the issue category. If there is a matching active incident, tell the caller
   we are already working on it, then still create a ticket so they are linked.
5. Call search_knowledge_base. Give at most one or two steps at a time and wait for the result.
   Only give steps the tool returns. Never invent fixes, SLAs, prices or technical facts.
6. If not fixed, or the knowledge base says to escalate, call create_ticket. Report facts honestly
   (scope, severity, flags). You do NOT choose the priority: the system does, and tells you what to say.
7. Read the ticket number back slowly, say what happens next, and ask if there is anything else.

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
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.caller: dict | None = None

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Greet the caller, say you are an AI service desk assistant, and ask for their name and company."
        )

    @function_tool
    async def identify_caller(self, context: RunContext, name: str, company: str, callback_number: str = "") -> str:
        """Look up the caller in client records. Recognized does not mean verified."""
        conn = db.connect()
        self.caller = tickets.identify_caller(conn, name, company, callback_number)
        if not self.caller["recognized"]:
            return ("Caller not found in client records. Continue helping, but do not share any ticket or account "
                    "details. Mark this in the ticket.")
        extra = " Their number does not match the number on file; note this in the ticket." \
            if callback_number and not self.caller["phone_matches_record"] else ""
        return f"Caller recognized as {self.caller['name']} at {self.caller['company_name']}. Not identity-verified.{extra}"

    @function_tool
    async def check_known_outages(self, context: RunContext, category: Category) -> str:
        """Check whether there is already an active incident in this category."""
        found = tickets.active_incidents(db.connect(), category)
        if not found:
            return "No active incidents in this category."
        return "Active incident: " + "; ".join(i["title"] for i in found)

    @function_tool
    async def search_knowledge_base(self, context: RunContext, issue_description: str) -> str:
        """Find first-line troubleshooting steps for the described issue."""
        hits = kb.search(issue_description)
        if not hits:
            return "No matching article. Offer to create a ticket."
        return "\n".join(kb.format_for_agent(h) for h in hits)

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
        r = await asyncio.to_thread(
            tickets.create,
            conn, caller_name=caller_name, company_name=company, callback_number=callback_number,
            category=category, summary=summary, details=details, scope=scope, severity=severity,
            workaround_available=workaround_available, security_concern=security_concern, data_loss=data_loss,
            caller_requests_human=caller_requests_human, caller_hostile=caller_hostile,
        )
        t, d = r["ticket"], r["decision"]
        logger.info("ticket %s P%s action=%s", t["ref"], t["priority"], t["action"])
        # Push to Zoho Desk off the event loop so the call audio is not blocked. Failures are logged, not spoken.
        sync = await asyncio.to_thread(zoho.sync_ticket, conn, r)
        if not sync.get("ok") and not sync.get("skipped"):
            logger.error("Zoho sync failed, ticket %s kept locally: %s", t["ref"], sync.get("error"))
        parts = [
            f"{'Created' if r['is_new'] else 'Updated existing'} ticket {t['ref']}, priority P{t['priority']}.",
            d.say_to_caller(),
            "Read the ticket number back slowly.",
        ]
        if d.caller_instructions:
            parts.append(d.caller_instructions)
        if r["incident"]:
            parts.append(f"Linked to active incident: {r['incident']['title']}.")
        if d.requires_verification:
            parts.append("Do not make any account change yourself.")
        return " ".join(parts)

    @function_tool
    async def get_ticket_status(self, context: RunContext, ticket_number: str) -> str:
        """Status of an existing ticket. Only for a recognized caller and tickets of their own company."""
        if not self.caller or not self.caller["recognized"]:
            return "Caller is not recognized. Do not share ticket details. Offer to take a message in a new ticket."
        conn = db.connect()
        tid = tickets.parse_ref(ticket_number)
        t = tickets.get(conn, tid) if tid else None
        if not t or t["company_id"] != self.caller["company_id"]:
            return "No matching ticket found for this caller's company."
        return (f"Ticket {t['ref']}: {t['summary']}. Status {t['status'].replace('_', ' ')}, priority P{t['priority']}, "
                f"assigned to {t['assignee'] or 'no one yet'}. Response due {t['ack_due']}.")

    @function_tool
    async def add_ticket_note(self, context: RunContext, ticket_number: str, note: str) -> str:
        """Add the caller's new information to their own company's ticket."""
        if not self.caller or not self.caller["recognized"]:
            return "Caller is not recognized. Do not modify tickets."
        conn = db.connect()
        tid = tickets.parse_ref(ticket_number)
        t = tickets.get(conn, tid) if tid else None
        if not t or t["company_id"] != self.caller["company_id"]:
            return "No matching ticket found for this caller's company."
        tickets.add_note(conn, tid, "voice-agent", note)
        return f"Note added to {t['ref']}."


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
    )
    await session.start(room=ctx.room, agent=MspSupportAgent())


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(
        entrypoint_fnc=entrypoint,
        prewarm_fnc=prewarm,
        # Default is 10s, which a first start on a laptop can exceed (imports + model load).
        initialize_process_timeout=60,
        num_idle_processes=1,
    ))

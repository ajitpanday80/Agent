import logging
import os
from typing import Literal

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession, RunContext, function_tool
from livekit.plugins import deepgram, openai, silero

from . import kb, tickets

load_dotenv()
logger = logging.getLogger("msp-agent")

INSTRUCTIONS = """\
You are the L1 service desk voice assistant for an IT managed service provider (MSP).
You are speaking on the phone, so keep replies short (one or two sentences), plain, and
never use lists, markdown or symbols. Ask one question at a time.

Start by telling the caller you are an AI assistant, then ask for their name, company and
a callback number, then ask what the problem is.

What you can do:
- Offer simple first-line fixes using the lookup_knowledge_base tool. Only give steps the tool returns.
- Create a ticket with create_ticket once you have name, company, callback number and a clear issue.
- Hand off to a human with escalate_to_human.

Rules:
- NEVER reset passwords, unlock accounts, reset MFA, or change access. These are common
  social-engineering targets. Create a ticket marked high priority and say a technician will
  verify their identity and call back.
- Escalate immediately for: outage affecting multiple users, security incidents (ransomware,
  phishing clicked, suspicious logins), the caller asking for a person, or an angry caller.
- If you do not know something, say so. Never guess fixes, prices or SLAs.
- Read the ticket number back clearly before ending the call.
"""


class MspSupportAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Greet the caller, say you are an AI service desk assistant, and ask for their name and company."
        )

    @function_tool
    async def lookup_knowledge_base(self, context: RunContext, issue_description: str) -> str:
        """Look up first-line troubleshooting steps for a described IT issue."""
        hits = kb.search(issue_description)
        if not hits:
            return "No matching article. Offer to create a ticket."
        return "\n".join(f"{h['title']}: {h['steps']}" for h in hits)

    @function_tool
    async def create_ticket(
        self,
        context: RunContext,
        caller_name: str,
        company: str,
        callback_number: str,
        summary: str,
        priority: Literal["low", "normal", "high", "urgent"],
        details: str = "",
    ) -> str:
        """Create a support ticket. Only call once name, company, callback number and issue are known."""
        ticket_id = tickets.create(caller_name, company, callback_number, summary, priority, details)
        logger.info("created ticket %s", ticket_id)
        return f"Ticket {ticket_id} created."

    @function_tool
    async def escalate_to_human(self, context: RunContext, reason: str) -> str:
        """Transfer the caller to a human technician. Use for outages, security incidents, or on request."""
        # TODO: with telephony, do a SIP warm transfer here.
        logger.warning("ESCALATION requested: %s", reason)
        return ("Escalation recorded. Tell the caller a technician will call them back shortly "
                "and confirm their callback number, since live transfer is not enabled yet.")


async def entrypoint(ctx: agents.JobContext) -> None:
    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="en-US"),
        llm=openai.LLM(
            model=os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5"),
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        ),
        tts=deepgram.TTS(model="aura-2-thalia-en"),
        vad=silero.VAD.load(),
    )
    await session.start(room=ctx.room, agent=MspSupportAgent())


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))

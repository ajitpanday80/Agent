import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Literal

from dotenv import load_dotenv
from livekit import agents
from livekit import api, rtc
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
1. Say you are an AI assistant. Use the Zoho caller-ID match in your greeting; ask only for name or company that Zoho did not identify.
   Use inbound caller ID as the callback number unless the caller provides another number. Do not ask for a number twice.
2. Call identify_caller with any known and caller-confirmed details. If not contact-matched, share no account or ticket details.
3. Ask what is wrong. Find out: how many people are affected, whether work is completely stopped,
   whether there is a workaround, and anything that looks like a security problem.
4. Call check_known_outages for the issue category. If there is a matching active incident, tell the caller
   we are already working on it, then still create a ticket so they are linked.
5. For device-specific issues, first confirm whether the caller is using Windows or macOS when that is not clear.
   Call search_knowledge_base. Give at most one or two steps at a time and wait for the result.
   Only give steps the tool returns. Never invent fixes, SLAs, prices or technical facts. If no matching article
   is returned, say you will arrange technician help instead of guessing.
6. If not fixed, or the knowledge base says to escalate, call create_ticket. Report facts honestly
   (scope, severity, flags). You do NOT choose the priority: the system does, and tells you what to say.
7. Read the ticket number back slowly, say what happens next, and ask if there is anything else.
8. If a knowledge-base step FIXED the problem and the caller confirms they need nothing else, call
   log_resolved_call so the call is recorded. Never use it for security or account-access issues.

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
- If the caller asks for a person, create/update the ticket and attempt the configured LiveKit human transfer.
- If the caller asks for voicemail, use send_to_voicemail. If forwarding is unavailable, offer to take a spoken message
  and use save_voicemail_message. Never claim a voicemail was recorded unless the tool confirms it.
- Before using save_voicemail_message, explain that speech will be transcribed and saved to Zoho Desk and ask for consent.
  If they decline, do not capture the message; create only a callback request with minimal details.
- Do not discuss other clients. Do not read out phone numbers or emails from records.
- Caller ID is not identity verification. A phone match may personalize the greeting and associate a ticket, but never disclose ticket/account details or perform access changes based on it.
- If you are unsure about anything, say so and create a ticket.
"""


class MspSupportAgent(Agent):
    def __init__(self, job_ctx: agents.JobContext | None = None, inbound_caller_number: str = "") -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.caller: dict | None = None
        self.job_ctx = job_ctx
        self.inbound_caller_number = inbound_caller_number.strip()
        self.pending_voicemail: tuple[str | None, int] | None = None

    @staticmethod
    def _destination_target(destination_kind: str) -> str:
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key=?", (f"{destination_kind}_destination",)
            ).fetchone()
            return str(row["value"]).strip() if row else ""
        finally:
            conn.close()

    async def _transfer_sip_call(self, destination_kind: str) -> tuple[bool, str]:
        """Use LiveKit SIP REFER to hand the active phone leg to a configured destination."""
        if not self.job_ctx:
            return False, "This session has no LiveKit phone call context."
        target = self._destination_target(destination_kind)
        if not target:
            return False, f"No {destination_kind.replace('_', ' ')} destination is configured."
        try:
            participant = await asyncio.wait_for(
                self.job_ctx.wait_for_participant(kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP), timeout=8
            )
            target_uri = target if target.lower().startswith("sip:") else f"tel:{target}"
            async with api.LiveKitAPI() as lk:
                await lk.sip.transfer_sip_participant(api.TransferSIPParticipantRequest(
                    room_name=self.job_ctx.room.name,
                    participant_identity=participant.identity,
                    transfer_to=target_uri,
                    play_dialtone=True,
                ))
            return True, "The LiveKit transfer completed."
        except Exception as error:
            logger.warning("LiveKit %s transfer failed: %s", destination_kind, error)
            return False, f"LiveKit transfer failed ({type(error).__name__})."

    async def on_enter(self) -> None:
        if self.inbound_caller_number:
            def lookup():
                conn = db.connect()
                try:
                    return tickets.identify_caller(conn, "", "", self.inbound_caller_number)
                finally:
                    conn.close()
            try:
                self.caller = await asyncio.wait_for(asyncio.to_thread(lookup), timeout=3.0)
            except Exception as error:
                logger.info("Inbound caller-ID lookup unavailable: %s", type(error).__name__)
        if self.caller and self.caller.get("recognized"):
            name, company = self.caller.get("name", ""), self.caller.get("company_name", "")
            if name and company:
                greeting = f"Greet them by saying: Hi {name}, thanks for calling {company} support. How can I help you today?"
            elif name:
                greeting = f"Greet them by saying: Hi {name}, thanks for calling MSP support. What company are you calling from?"
            else:
                greeting = "Say you are the AI service desk assistant and ask for the caller's name."
        elif self.caller and self.caller.get("company_recognized"):
            company = self.caller.get("company_name", "")
            greeting = f"Thank them for calling {company} support and ask only for their name."
        else:
            greeting = "Say you are the AI service desk assistant and ask for the caller's name and company."
        await self.session.generate_reply(
            instructions="Say you are an AI service desk assistant. " + greeting,
            allow_interruptions=False,  # noise or echo must not cut the greeting off
        )

    @function_tool
    async def identify_caller(self, context: RunContext, name: str, company: str, callback_number: str = "") -> str:
        """Look up the caller in client records. Recognized does not mean verified."""
        conn = db.connect()
        callback_number = callback_number.strip() or self.inbound_caller_number
        try:
            self.caller = await asyncio.to_thread(tickets.identify_caller, conn, name, company, callback_number)
        finally:
            conn.close()
        if not self.caller["recognized"]:
            if self.caller.get("company_recognized"):
                return (f"Company matched to Zoho account {self.caller['company_name']}; caller is not contact-matched or identity-verified. "
                        "Share no ticket or contact details.")
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
        company_name = self.caller.get("company_name") if self.caller and self.caller.get("recognized") else None
        hits = kb.search_index(issue_description, db.connect(), company_name=company_name)
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
        callback_number = callback_number.strip() or self.inbound_caller_number
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
            cur = conn.execute("INSERT INTO handoffs(kind,summary,callback_number,transcript,created_at,updated_at) "
                               "VALUES('zoho_unavailable',?,?,?,?,?)",
                               (summary, callback_number,
                                f"Caller {caller_name} at {company}. {category}; {scope}; {severity}; {details}; {e}", now, now))
            conn.commit()
            if caller_requests_human:
                transferred = False
                if self._destination_target("human"):
                    announcement = await context.session.say("I'll try to connect you to a human agent now.",
                                                             allow_interruptions=False)
                    await announcement.wait_for_playout()
                    transferred, _ = await self._transfer_sip_call("human")
                if transferred:
                    conn.execute("UPDATE handoffs SET status='completed',updated_at=? WHERE id=?",
                                 (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), cur.lastrowid))
                    conn.commit()
                    return "Zoho Desk is unavailable. Your request is saved in local staff intake, and the call was transferred to a human agent."
                return "Zoho Desk is unavailable and human transfer did not complete. Your request is saved in local staff intake; please leave a message or call back."
            return ("Zoho Desk could not create the ticket. I saved the request in the staff intake queue, "
                    "but there is no Zoho ticket number yet. A technician must follow up.")
        t, d = r["ticket"], r["decision"]
        logger.info("ticket %s P%s action=%s", t["ref"], t["priority"], t["action"])
        handoff_id = None
        if d.action in ("transfer_to_human", "page_oncall", "callback_next_business_day", "voicemail"):
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            cur = conn.execute("INSERT INTO handoffs(ticket_id,kind,summary,callback_number,transcript,created_at,updated_at) "
                               "VALUES (?,?,?,?,?,?,?)",
                               (t["id"], d.action, t["summary"], callback_number, details, now, now))
            handoff_id = cur.lastrowid
            conn.commit()
        transfer_result = ""
        destination_kind = ("human" if caller_requests_human or d.action == "transfer_to_human"
                            else "voicemail" if d.action == "voicemail" else "")
        if destination_kind:
            if self._destination_target(destination_kind):
                if destination_kind == "human":
                    announcement = await context.session.say("I'll try to connect you to a human agent now.",
                                                             allow_interruptions=False)
                else:
                    announcement = await context.session.say("I'll forward this call to the configured voicemail system now.",
                                                             allow_interruptions=False)
                await announcement.wait_for_playout()
            transferred, _ = await self._transfer_sip_call(destination_kind)
            if destination_kind == "human":
                if transferred and handoff_id:
                    conn.execute("UPDATE handoffs SET status='completed',updated_at=? WHERE id=?",
                                 (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), handoff_id))
                    conn.commit()
                transfer_result = (
                    " The caller was transferred to the configured human agent." if transferred else
                    " Human transfer did not complete; tell the caller the request remains in the staff queue and offer voicemail or a transcript message."
                )
            elif not transferred:
                transfer_result = (" Voicemail transfer did not complete; tell the caller the request is in the staff queue "
                                   "and offer to save a spoken message using save_voicemail_message.")
            elif handoff_id:
                conn.execute("UPDATE handoffs SET transcript=?,updated_at=? WHERE id=?",
                             ("Forwarded to the configured external voicemail system; retrieve audio from that mailbox.",
                              datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), handoff_id))
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
        if transfer_result:
            parts.append(transfer_result)
        return " ".join(parts)

    @function_tool
    async def send_to_voicemail(
        self, context: RunContext, caller_name: str, company: str, callback_number: str,
        message_summary: str,
    ) -> str:
        """Create a Zoho ticket, then transfer the active call to the configured voicemail destination."""
        if not message_summary.strip():
            return "Ask the caller for a short reason for the call before routing them."
        callback_number = callback_number.strip() or self.inbound_caller_number
        conn = db.connect()
        summary = f"Caller requested voicemail: {message_summary.strip()}"[:250]
        try:
            result = await asyncio.to_thread(
                tickets.create, conn, caller_name=caller_name, company_name=company,
                callback_number=callback_number, category="other", summary=summary,
                details="Caller requested transfer to the MSP voicemail destination.",
                scope="single_user", severity="degraded",
            )
        except Exception:
            logger.exception("Could not create Zoho ticket for voicemail request")
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            cur = conn.execute("INSERT INTO handoffs(kind,summary,callback_number,transcript,created_at,updated_at) "
                               "VALUES('zoho_unavailable',?,?, '',?,?)",
                               (summary, callback_number, now, now))
            conn.commit()
            self.pending_voicemail = (None, int(cur.lastrowid))
            if self._destination_target("voicemail"):
                announcement = await context.session.say(
                    "I'll connect you to the voicemail system now. Please leave your message after its greeting.",
                    allow_interruptions=False,
                )
                await announcement.wait_for_playout()
                transferred, _ = await self._transfer_sip_call("voicemail")
                if transferred:
                    return "Zoho Desk is unavailable, but I saved a local staff intake record and transferred you to the voicemail system."
            return "Zoho Desk is unavailable and voicemail transfer did not complete. Your request is saved in local staff intake; ask the caller to dictate a message and save it with save_voicemail_message."
        ticket = result["ticket"]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = conn.execute("INSERT INTO handoffs(ticket_id,kind,status,summary,callback_number,transcript,created_at,updated_at) "
                     "VALUES(?, 'voicemail', 'queued', ?, ?, 'Transfer pending.', ?, ?)",
                     (ticket["id"], summary, callback_number, now, now))
        conn.commit()
        self.pending_voicemail = (str(ticket["id"]), int(cur.lastrowid))
        if self._destination_target("voicemail"):
            announcement = await context.session.say(
                "I'll connect you to the voicemail system now. Please leave your message after its greeting.",
                allow_interruptions=False,
            )
            await announcement.wait_for_playout()
        transferred, _ = await self._transfer_sip_call("voicemail")
        if transferred:
            conn.execute("UPDATE handoffs SET transcript=?,updated_at=? WHERE id=?",
                         ("Forwarded to the configured external voicemail system; retrieve audio from that mailbox.",
                          datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), cur.lastrowid))
            conn.commit()
            return f"Ticket {ticket['ref']} is created and the call was transferred to the configured voicemail destination."
        conn.execute("UPDATE handoffs SET transcript=?,updated_at=? WHERE id=?",
                     ("Voicemail transfer failed; agent offered to capture a speech transcript.",
                      datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), cur.lastrowid))
        conn.commit()
        return (f"Ticket {ticket['ref']} is created, but voicemail transfer did not complete. "
                "Ask the caller to dictate their message, then save it with save_voicemail_message. Do not disconnect them.")

    @function_tool
    async def save_voicemail_message(
        self, context: RunContext, caller_name: str, company: str, callback_number: str,
        message_summary: str, spoken_message: str,
    ) -> str:
        """Save a caller's spoken message as a Zoho ticket and internal handoff when mailbox transfer is unavailable."""
        if not message_summary.strip() or not spoken_message.strip():
            return "Ask the caller to dictate a brief message and summarize the issue before saving it."
        callback_number = callback_number.strip() or self.inbound_caller_number
        spoken_message = spoken_message.strip()[:5000]
        if self.pending_voicemail:
            ticket_id, handoff_id = self.pending_voicemail
            zoho_updated = False
            if ticket_id:
                try:
                    from . import zoho
                    await asyncio.to_thread(
                        zoho.add_comment, ticket_id,
                        "Caller-authorized voicemail transcript (speech recognition; verify before acting):\n\n"
                        + spoken_message, public=False,
                    )
                    zoho_updated = True
                except Exception:
                    logger.exception("Could not add voicemail transcript to Zoho ticket")
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn = db.connect()
            conn.execute("UPDATE handoffs SET kind='voicemail_transcript',transcript=?,updated_at=? WHERE id=?",
                         (spoken_message, now, handoff_id))
            conn.commit()
            self.pending_voicemail = None
            if zoho_updated:
                return f"The message has been added to Zoho ticket {ticket_id} and the staff handoff queue."
            return ("The message is saved in the local staff handoff queue. Zoho Desk was unavailable, so staff must create "
                    "a Zoho ticket and attach the transcript before acting.")
        conn = db.connect()
        summary = f"Voicemail transcript: {message_summary.strip()}"[:250]
        try:
            result = await asyncio.to_thread(
                tickets.create, conn, caller_name=caller_name, company_name=company,
                callback_number=callback_number, category="other", summary=summary,
                details="Caller message captured by speech recognition. Verify details with caller before acting.\n\n"
                        + spoken_message.strip(), scope="single_user", severity="degraded",
            )
        except Exception:
            logger.exception("Could not create Zoho ticket for transcribed voicemail")
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute("INSERT INTO handoffs(kind,summary,callback_number,transcript,created_at,updated_at) "
                         "VALUES('zoho_unavailable',?,?,?,?,?)",
                         (summary, callback_number, spoken_message.strip(), now, now))
            conn.commit()
            return "Zoho Desk is unavailable. The message is in local staff intake without a Zoho ticket number."
        ticket = result["ticket"]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute("INSERT INTO handoffs(ticket_id,kind,status,summary,callback_number,transcript,created_at,updated_at) "
                     "VALUES(?, 'voicemail_transcript', 'queued', ?, ?, ?, ?, ?)",
                     (ticket["id"], summary, callback_number, spoken_message.strip(), now, now))
        conn.commit()
        return f"The message has been saved as Zoho ticket {ticket['ref']} and is in the staff follow-up queue."

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
        callback_number = callback_number.strip() or self.inbound_caller_number
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
            # Reduced interruption sensitivity to lower LiveKit adaptive interruption API usage
            # min_duration: require longer speech before treating it as an interruption (1.2s vs 0.8s)
            # min_words: require more words before interrupting (3 vs 2)
            # This prevents accidental interruptions from background noise, coughs, or brief utterances
            "interruption": {"min_duration": 1.2, "min_words": 3},
        },
    )

    # Diagnostics: shows in the terminal what the agent hears and when it starts/stops speaking.
    @session.on("user_input_transcribed")
    def _heard(ev) -> None:
        if ev.is_final:
            logger.info("HEARD: %r", ev.transcript)

    @session.on("agent_state_changed")
    def _state(ev) -> None:
        logger.info("agent %s -> %s", ev.old_state, ev.new_state)

    @session.on("error")
    def _error(ev) -> None:
        logger.error("session error: %s", ev.error)

    # Wait for the caller first so SIP caller ID can be used for a Zoho lookup and tailored greeting.
    participant = await ctx.wait_for_participant()
    inbound_number = ""
    if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
        inbound_number = participant.attributes.get("sip.phoneNumber", "")
    await session.start(room=ctx.room, agent=MspSupportAgent(ctx, inbound_number))


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(
        entrypoint_fnc=entrypoint,
        prewarm_fnc=prewarm,
        agent_name=os.getenv("LIVEKIT_AGENT_NAME", "msp-helpdesk"),
        # Default is 10s, which a first start on a laptop can exceed (imports + model load).
        initialize_process_timeout=60,
        num_idle_processes=1,
    ))

"""Production readiness gate.  python -m msp_agent.preflight   (exit code 1 if any blocker)

With MSP_ENV=production the agent runs this at start-up and refuses to start while any BLOCKER fails.
It checks configuration and live connectivity. It cannot check telephony or legal compliance, so those are
explicit attestations that a person must set.
"""
import os
import sys
from dataclasses import dataclass

from . import escalation, kb, zoho

REQUIRED_KEYS = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "DEEPGRAM_API_KEY", "OPENROUTER_API_KEY")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    blocker: bool = True


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() == "true"


def run() -> list[Check]:
    c: list[Check] = []
    prod = os.getenv("MSP_ENV", "").lower() == "production"
    c.append(Check("MSP_ENV=production", prod, "set MSP_ENV=production" if not prod else "ok"))

    missing = [k for k in REQUIRED_KEYS if not os.getenv(k, "").strip() or "PASTE" in os.getenv(k, "")]
    c.append(Check("LiveKit, Deepgram, OpenRouter keys", not missing, f"missing: {', '.join(missing)}" if missing else "ok"))

    # Zoho: configured and reachable, with real contacts
    if not zoho.enabled():
        c.append(Check("Zoho Desk configured", False, "ZOHO_* values missing or ZOHO_SYNC=false: production tickets must live in Zoho"))
    else:
        try:
            st, resp = zoho.api("GET", "/organizations", org=False)
            ok = st == 200 and any(str(o.get("id")) == os.getenv("ZOHO_DESK_ORG_ID") for o in resp.get("data", []))
            c.append(Check("Zoho Desk reachable and org id matches", ok, "ok" if ok else f"status {st}: {str(resp)[:120]}"))
            if ok:
                real = [x for x in zoho.directory(force=True) if (x.get("account") or {}).get("accountName")]
                c.append(Check("Zoho has client contacts with a company", bool(real),
                               f"{len(real)} usable contacts" if real else
                               "no contact has an Account, so every caller would be 'unrecognized'"))
        except Exception as e:  # noqa: BLE001
            c.append(Check("Zoho Desk reachable", False, str(e)[:160]))

    # Knowledge base: human-reviewed only
    drafts = [a["id"] for a in kb.ARTICLES if a["status"] != "reviewed"]
    reviewed = len(kb.ARTICLES) - len(drafts)
    c.append(Check("KB_REQUIRE_REVIEWED=true", _flag("KB_REQUIRE_REVIEWED"), "set KB_REQUIRE_REVIEWED=true so drafts are never used"))
    c.append(Check("At least one human-reviewed KB article", reviewed > 0,
                   f"{reviewed} reviewed, {len(drafts)} draft (drafts are ignored in production)"))

    c.append(Check("SLA targets set from your contracts", not escalation.SLA_IS_DEFAULT,
                   "set MSP_SLA (priority:ack_minutes/resolve_hours); defaults are placeholders"))
    c.append(Check("Business hours and time zone set", bool(os.getenv("MSP_TZ") and os.getenv("MSP_HOURS")),
                   "set MSP_TZ (e.g. Asia/Kolkata) and MSP_HOURS (e.g. 09:00-18:00)"))
    c.append(Check("Live transfer enabled or callback-only accepted",
                   _flag("LIVE_TRANSFER_ENABLED") or _flag("ALLOW_CALLBACK_ONLY"),
                   "live transfer is not built; set ALLOW_CALLBACK_ONLY=true to accept callback promises"))
    c.append(Check("Telephony verified by a person", _flag("TELEPHONY_VERIFIED"),
                   "set TELEPHONY_VERIFIED=true after a real inbound test call reaches the agent"))
    c.append(Check("Legal review done (recording, AI disclosure)", _flag("LEGAL_REVIEWED"),
                   "set LEGAL_REVIEWED=true only after you or counsel confirmed local rules"))
    return c


def enforce() -> None:
    """Called at start-up in production. Exits if any blocker fails."""
    if os.getenv("MSP_ENV", "").lower() != "production":
        return
    failed = [x for x in run() if x.blocker and not x.ok]
    if failed:
        print("REFUSING TO START: production checks failed:", file=sys.stderr)
        for x in failed:
            print(f"  - {x.name}: {x.detail}", file=sys.stderr)
        raise SystemExit(1)


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    checks = run()
    for x in checks:
        print(f"[{'PASS' if x.ok else 'FAIL'}] {x.name}: {x.detail}")
    n = sum(1 for x in checks if x.blocker and not x.ok)
    print(f"\n{n} blocker(s)." if n else "\nAll checks passed.")
    raise SystemExit(1 if n else 0)


if __name__ == "__main__":
    main()

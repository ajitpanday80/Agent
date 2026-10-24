# Reviewing a knowledge-base article

Every article in `kb/` ships as `status: draft`. Drafts are ignored when `KB_REQUIRE_REVIEWED=true` (production).
An article becomes usable only when a named person approves it:

    python -m msp_agent.kb_review list
    python -m msp_agent.kb_review approve <article-id> --by "Your Name"

Do NOT approve an article you have not checked. The drafts were written from search summaries of the cited vendor
pages, not from a technician's hands-on use.

## Checklist (per article)
1. **Open every source URL.** Do the steps match the current page? Menu names change between Windows/macOS/Office
   versions. Fix anything that differs.
2. **Try it.** Follow the caller steps on a real Windows PC / Mac you control. Can a non-technical person do each
   step from a spoken instruction? Rewrite steps that need technical knowledge.
3. **Safety.** No step may change accounts, passwords, MFA, firewall, registry, drivers or delete data. Anything
   like that belongs under "Technician notes", which the agent never reads to callers.
4. **Wrong-advice test.** Could any step make things worse if the diagnosis is wrong (e.g. restarting the router for
   everyone, force-quitting an app with unsaved work)? Is the warning in the step?
5. **Your environment.** Add your own tools and clients' specifics (VPN client name, printer naming, ticket rules).
6. **Escalation.** Is "Escalate if" right for your SLAs? Does it say when to flag several users or a security concern?
7. **Numbers.** No time limits, retention periods or versions unless verified for your tenant.
8. Add 2 or more realistic phrasings to `msp_agent/kb_eval.py`, then run `python -m msp_agent.kb_eval`.

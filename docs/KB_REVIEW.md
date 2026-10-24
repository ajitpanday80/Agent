# Reviewing a knowledge-base article

The starter library contains a conservative set of shared, caller-safe articles marked `status: approved`; the remaining articles stay disabled drafts. Approved starter articles are original RelayDesk summaries linked to vendor guidance. They are general guidance and have not been exercised against every customer's tenant, device build, or MSP contract. Check them against the current sources and your support environment before relying on them for a customer-specific procedure.

New or changed articles should stay drafts until a named administrator approves them in the dashboard:

    python -m msp_agent.kb_review list
    python -m msp_agent.kb_review approve <article-id> --by "Your Name"

Do not approve an article until you have checked it. A dashboard approval records the signed-in administrator and timestamp.

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

## Published starter coverage

The initial enabled set focuses on common Windows/macOS Wi-Fi, printing, Microsoft 365 mail and OneDrive, VPN,
Teams audio, Bluetooth, keyboard/mouse, and basic performance issues. Access recovery, security incidents, data
recovery, Windows update repair, boot failures, and other higher-risk procedures remain drafts or are handled by
the agent's escalation rules. Add client-specific runbooks only after documenting the exact client and validating
the steps with the relevant tools and permissions.

---
id: lost-phone-mfa
title: Lost or replaced phone, cannot approve sign-in (MFA)
category: access
os: any
keywords: mfa, authenticator, lost phone, new phone, two factor, 2fa, verification code, approve sign-in, stolen phone
sources:
  - https://learn.microsoft.com/en-us/answers/questions/5428281/cant-access-my-microsoft-account-because-i-lost-my
drafted: 2026-10-22
reviewed: never
status: draft
---
## Steps
1. Do NOT reset MFA. This is a high-risk request for impersonation.
2. Ask if they can sign in with another method they registered before, such as a text or call. If so, they can add the new phone at their security info page.
3. Otherwise create an access ticket. A technician must verify identity, then an administrator can have the user re-register or issue a temporary access pass.
4. If the phone was stolen, tell the caller a technician will treat this as a security matter.

## Escalate if
Stolen phone means treat as a security incident. Any attempt to bypass verification is a reason to escalate.

## Technician notes
Source is a community Q&A thread; confirm the admin procedure against Microsoft Entra documentation for your tenant.

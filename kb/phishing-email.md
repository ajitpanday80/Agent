---
id: phishing-email
title: Suspicious email or text (phishing)
category: other
os: any
keywords: phishing, suspicious email, spam, scam email, fake email, suspicious link, fake invoice, clicked a link, clicked link, opened attachment, entered password, suspicious text, smishing
sources:
  - https://consumer.ftc.gov/articles/how-recognize-avoid-phishing-scams
  - https://www.cisa.gov/audiences/small-and-medium-businesses/secure-your-business/teach-employees-avoid-phishing
drafted: 2026-10-22
reviewed: never
status: draft
---
## Steps
1. Ask first: did they click a link, open an attachment, or enter a password or other details? The answer decides everything.
2. If they did NOT click or open anything: tell them not to click links or open attachments, not to reply, and not to forward it to colleagues.
3. Tell them the ticket is the report, and a technician will review the message. If they want to check whether it is real, they should contact the company using a phone number or website they already know, never the details in the email.
4. If they DID click, open an attachment, or enter a password: this is a security incident. Create the ticket with security concern set to true, tell them not to enter anything further, and say a technician will call right away.
5. If they entered a password, a technician will help secure the account. They can also change that password themselves through the company's normal sign-in page, never through a link in the email.
6. If they gave out financial or identity information, a technician will advise on next steps, including the FTC identity theft site, IdentityTheft.gov.

## Escalate if
Anything was clicked, opened or entered: always escalate as a security incident. Several people received or clicked the same email: set scope to several users.

## Technician notes
Collect sender, subject, time, and which links or attachments were used. Scan the endpoint with updated security software, review sign-in activity for the affected account, and force a password reset and session revocation if credentials were entered. Report to the Anti-Phishing Working Group (reportphishing@apwg.org) and the FTC (ReportFraud.ftc.gov) as policy allows.

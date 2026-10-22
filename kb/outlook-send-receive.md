---
id: outlook-send-receive
title: Outlook cannot send or receive email
category: email
os: windows
keywords: outlook, can't send, cannot send, can't receive, not receiving email, email stuck, outbox, disconnected, working offline, trying to connect, email not sending, email not arriving, mail not syncing, password prompt, keeps asking password, not receiving any emails, not receiving emails, no new emails, emails not coming in, email not arriving
sources:
  - https://support.microsoft.com/en-us/outlook/i-can-t-send-or-receive-messages-in-outlook
  - https://support.microsoft.com/en-us/outlook/outlook-email-stuck
  - https://support.microsoft.com/en-us/outlook/fix-your-outlook-email-connection-by-repairing-your-profile
drafted: 2026-10-22
reviewed: never
status: draft
---
## Steps
1. Ask what Outlook shows at the bottom of the window. Disconnected, Working Offline or Trying to connect means Outlook cannot reach the mail server.
2. Ask them to open a web browser and visit any website. If no website loads, this is a network problem; use the Wi-Fi and internet article instead.
3. If the status says Working Offline, ask them to open the Send and Receive tab and click Work Offline to turn it off.
4. If a message is stuck in the Outbox, ask whether it has a large attachment. Ask them to open it, remove the attachment, and send a link to the file instead.
5. If they recently changed their password, Outlook may need the new one. Ask them to type it when prompted; never read passwords aloud.
6. Ask them to close Outlook fully and open it again. Then restart the computer if it still fails.

## Escalate if
Still failing after a restart; several people cannot send or receive (possible mail service problem: set scope to several users or whole company); a repeated password prompt that does not stop; or the error mentions the outgoing server. Mailbox full or storage full needs a technician.

## Technician notes
From Microsoft's guides: check account storage and mailbox quota; classic Outlook has a Connectivity troubleshooter; File, Account Settings, select the account, Repair (not available for Outlook 2016 with Exchange); start Outlook in safe mode (outlook.exe /safe) to rule out add-ins; use the Inbox Repair tool for a damaged .pst; create a new profile if the profile is corrupt. For POP/IMAP accounts check that SMTP authentication is on and that third-party accounts use an app password. Microsoft notes some of these pages are dated, so confirm against the user's Outlook version.

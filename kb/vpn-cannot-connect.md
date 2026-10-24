---
id: vpn-cannot-connect
title: VPN will not connect
category: network_vpn
os: any
keywords: vpn, remote access, work from home, error 720, error 800, error 809, error 721, error 812, can't connect
sources:
  - https://learn.microsoft.com/en-us/troubleshoot/windows-client/networking/error-when-establishing-vpn-connection
  - https://learn.microsoft.com/en-us/troubleshoot/windows-server/networking/troubleshoot-error-720-when-establishing-a-vpn-connection
drafted: 2026-10-22
reviewed: never
status: approved
license: Internal-Original
attribution: RelayDesk original runbook; paraphrased and safety-scoped from the official source links listed above. No vendor text copied.
reviewed_by: RelayDesk KB editorial curation (2026-10-24)
---
## Steps
1. First ask for the exact error message or number, and whether regular internet works without the VPN.
2. Disconnect, close the VPN app fully, reopen it and try once more. Restart the computer if that fails.
3. Ask if they are on a hotel, cafe or other public network. Some networks block VPN traffic; trying a phone hotspot is a good test.

## Escalate if
Same error after these steps, or several users cannot connect. Record the exact error and when it began for the technician; several users may indicate a shared VPN service issue.

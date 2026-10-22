---
id: remote-desktop-cannot-connect
title: Cannot connect with Remote Desktop to the office computer
category: network_vpn
os: windows
keywords: remote desktop, rdp, can't connect to remote computer, remote computer, work computer from home, mstsc, remote desktop error, remote access, connect to my pc, office pc
sources:
  - https://learn.microsoft.com/en-us/troubleshoot/windows-server/remote/remote-desktop-cannot-connect-remote-computer
  - https://learn.microsoft.com/en-us/windows-server/remote/remote-desktop-services/remotepc/remote-pc-connections-faq
drafted: 2026-10-22
reviewed: never
status: draft
---
## Steps
1. Ask for the exact error message and the name or address they are connecting to.
2. Ask whether the office computer is turned on and not asleep or hibernating. Remote Desktop cannot connect to a computer that is off or asleep. If someone is in the office, ask them to wake it up.
3. Ask whether the caller's own internet works, and whether they need to connect to the company VPN first. Use the VPN article if the VPN will not connect.
4. If they reach the sign-in screen but cannot get in, their account may not be allowed for Remote Desktop on that computer. That needs a technician.
5. Ask whether it worked before, and what changed: a restart, an update, a new network.

## Escalate if
The computer cannot be reached after these checks, several people cannot connect (possible firewall, gateway or server problem: set scope to several users), or access permission is needed. Never ask the caller to change firewall or Remote Desktop settings.

## Technician notes
From Microsoft's guide: check that the Remote Desktop Services (TermService) and UserMode Port Redirector (UmRdpService) services are running; test TCP port 3389 from a machine on the same network with Test-NetConnection; check inbound firewall rules for Remote Desktop on all profiles and any third-party firewall or Azure Network Security Group; if the IP address works but the name does not, it is DNS; check the user is in Remote Desktop Users or an admin group; a network profile that reset to Public can block inbound RDP; antivirus can interfere, so test briefly with it disabled only as a diagnostic. Turning off Network Level Authentication weakens security and should only be a temporary test.

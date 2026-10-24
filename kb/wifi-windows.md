---
id: wifi-windows
title: Wi-Fi not connecting or no internet (Windows)
category: network_vpn
os: windows
keywords: wi-fi, wifi, wireless, no internet, internet down, not connecting, can't connect to wifi, network, connected but no internet, keeps disconnecting, dropping
sources:
  - https://support.microsoft.com/en-us/windows/experience/connectivity-networking/fix-wi-fi-connection-issues-in-windows
drafted: 2026-10-22
reviewed: never
status: draft
---
## Steps
1. First find the scope: ask whether other devices, such as a phone, can use the same Wi-Fi, and whether anyone else in the office has the problem.
2. Ask the caller to check that Wi-Fi is turned on from the network icon in the taskbar, that airplane mode is off, and that any physical Wi-Fi switch on the laptop is on.
3. Ask them to disconnect from the network and connect again. If asked for the password, they must type it themselves; never read it out.
4. Restart the computer and try again.
5. If other devices are also offline, the problem is the router or the internet provider.
6. Ask them to restart the modem and router only if nobody is in the middle of important work, because it disconnects everyone. Unplug for about two minutes, plug back in and wait a few minutes.
7. If the router has two bands, 2.4 and 5 gigahertz, ask them to try the other one.

## Escalate if
Several devices or several people are offline after the router restart (likely the internet provider or the router; set scope to several users or whole company). Or only this computer fails while other devices work. Or the Wi-Fi option is missing entirely.

## Technician notes
From Microsoft's guide: forget the network and rejoin it; run `netsh winsock reset` from an administrator prompt; if connected with no internet, `ipconfig /release`, `/renew`, `/flushdns` and turn off any manual proxy; an address starting 169.254 means the computer did not get an address from the router; for frequent drops set the wireless adapter power plan to maximum performance and try a different router channel; if the Wi-Fi option is missing, re-enable the adapter in Device Manager or restart the WLAN AutoConfig service; update the wireless driver from the manufacturer. Do not walk a non-technical caller through these.

---
id: windows-update-stuck
title: Windows Update stuck or failing
category: software
os: windows
keywords: windows update, update stuck, updating, stuck at 100%, stuck at 0%, update failed, failure configuring windows updates, reverting changes, update error, restart to update, pending restart, 0x
sources:
  - https://support.microsoft.com/en-us/windows/deployment/updates-lifecycle/troubleshoot-problems-updating-windows
  - https://support.microsoft.com/en-us/windows/windows-update-troubleshooter-19bc41ca-ad72-ae67-af3c-89ce169755dd
drafted: 2026-10-22
reviewed: never
status: draft
---
## Steps
1. Ask what the screen shows right now and whether there is an error code that starts with 0x. Write it down for the ticket.
2. If the computer is showing a progress screen, tell them not to switch it off, and to wait. Microsoft's guidance for an upgrade that seems stuck is to wait a long time before intervening.
3. If the update has clearly stopped for a long while, ask them to restart the computer once.
4. Ask them to unplug USB drives and memory cards, which can interfere with restarts.
5. Once Windows is running, ask them to open Settings, Windows Update, and check for updates again. Windows 11 also has a built-in Get Help troubleshooter for updates.
6. Ask whether the computer has plenty of free disk space and a working internet connection.

## Escalate if
The same update fails again, there is a repeating error code, the computer will not start, or many computers fail the same update (set scope to several users). Do not ask the caller to edit system folders or run repair commands.

## Technician notes
From Microsoft's guide: run the Windows Update troubleshooter (Get Help app on Windows 11); confirm enough free space for an upgrade; ensure the Windows Update service is running; stop the service and clear the SoftwareDistribution folder if the cache is corrupt; correct date and time; run `sfc /scannow` in an elevated prompt for corruption; temporarily pause third-party antivirus if it blocks the install; 0x80070422 points to a stopped or disabled update service; 0xC1900101 points to an incompatible driver (uninstall the network or graphics driver and try a clean boot); a repair reinstall through Windows Update keeps apps and files. Windows 10 reached end of support in October 2025, so advise on a Windows 11 upgrade where relevant.

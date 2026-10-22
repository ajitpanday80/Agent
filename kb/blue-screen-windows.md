---
id: blue-screen-windows
title: Blue screen or computer keeps restarting (Windows)
category: hardware
os: windows
keywords: blue screen, bsod, stop code, your device ran into a problem, keeps restarting, restarts by itself, crashes, unexpected restart, critical process died, kernel, boot loop
sources:
  - https://support.microsoft.com/en-us/windows/experience/performance-optimization/troubleshooting-windows-unexpected-restarts-and-stop-code-errors
  - https://support.microsoft.com/en-us/topic/60b01860-58f2-be66-7516-5c45a66ae3c6
drafted: 2026-10-22
reviewed: never
status: draft
---
## Steps
1. Ask for the exact words and stop code on the blue screen, for example a name in capital letters with underscores, and ask them to take a photo with a phone. Note it for the ticket.
2. Ask whether they recently added hardware, such as a new drive, memory or a USB device. If so, ask them to shut down, remove it, and start the computer again.
3. Ask whether it started after a recent update or driver change. Note it for the ticket.
4. Ask them to let the computer restart once on its own and see whether it comes back to Windows. If it does, ask them to save their work and keep it connected to power.
5. Ask how often it happens and what they were doing when it appeared.

## Escalate if
It happens more than once, the computer will not start, or files are at risk. Always create a ticket for a repeat blue screen, because drivers and hardware need a technician. If the computer shows a disk or boot error, treat it as hardware. Do not ask the caller to use Safe Mode, Startup Repair or System Restore by phone.

## Technician notes
From Microsoft's guides: remove recently added hardware; boot to Safe Mode if needed; check Device Manager for devices with warning icons; keep 10 to 15 percent of the disk free; install updates and check BIOS and firmware; run hardware and memory tests; use a system restore point or Startup Repair for boot problems. Microsoft says faulty drivers cause the majority of stop errors. Search the exact stop code, and analyze the memory dump with WinDbg (`!analyze -v`) to find the faulty module; Driver Verifier can test a suspect driver and can be disabled from Safe Mode. A named driver in a DPC_WATCHDOG_VIOLATION error can be disabled to isolate the problem.

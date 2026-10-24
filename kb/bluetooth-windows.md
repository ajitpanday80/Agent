---
id: bluetooth-windows
title: Bluetooth device will not connect (Windows)
category: hardware
os: windows
keywords: bluetooth, pair, pairing, bluetooth headset, bluetooth mouse, bluetooth keyboard, not connecting, keeps disconnecting, bluetooth missing, bluetooth not working
sources:
  - https://support.microsoft.com/en-us/windows/hardware/bluetooth/fix-bluetooth-problems-in-windows
  - https://support.microsoft.com/en-us/windows/hardware/bluetooth/troubleshoot-bluetooth-not-connecting-in-windows
  - https://support.microsoft.com/en-us/windows/hardware/bluetooth/bluetooth-keeps-disconnecting-in-windows
drafted: 2026-10-22
reviewed: never
status: approved
license: Internal-Original
attribution: RelayDesk original runbook; paraphrased and safety-scoped from the official source links listed above. No vendor text copied.
reviewed_by: RelayDesk KB editorial curation (2026-10-24)
---
## Steps
1. Ask which device it is, and whether it worked before.
2. Ask them to check the device is charged, switched on, and close to the computer.
3. Ask them to check that airplane mode is off and Bluetooth is turned on in Settings.
4. Ask them to turn Bluetooth off, wait about ten seconds, and turn it back on.
5. Ask them to remove the device in Settings, Bluetooth and devices, and pair it again.
6. Ask them to move other Bluetooth devices and USB 3 devices away, since they can cause interference.
7. Restart the computer and try again.

## Escalate if
Bluetooth is missing from Settings entirely, or the device drops again after pairing. That usually needs a driver or power-setting change from a technician.

## Technician notes
From Microsoft's guides: run the Bluetooth troubleshooter; update, reinstall or roll back the Bluetooth driver in Device Manager; if the Bluetooth option has disappeared, shut down fully (hold Shift while choosing Shut down) rather than restarting; turn off power-saving on the adapter's Power Management tab and restart the Bluetooth Support Service for disconnects; check for firmware and BIOS updates from the PC maker.

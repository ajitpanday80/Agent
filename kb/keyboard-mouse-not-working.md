---
id: keyboard-mouse-not-working
title: Keyboard or mouse not working
category: hardware
os: any
keywords: keyboard, mouse, trackpad, touchpad, keys not working, cursor frozen, can't type, wireless mouse, wireless keyboard, usb, dongle, batteries
sources:
  - https://support.microsoft.com/en-us/windows/hardware/input-devices/mouse-and-keyboard-problems-in-windows
  - https://support.microsoft.com/en-us/windows/fix-touchpad-problems-in-windows-30b498e5-0caa-9740-2b21-336ea75ee756
drafted: 2026-10-22
reviewed: never
status: approved
license: Internal-Original
attribution: RelayDesk original runbook; paraphrased and safety-scoped from the official source links listed above. No vendor text copied.
reviewed_by: RelayDesk KB editorial curation (2026-10-24)
---
## Steps
1. Ask which device it is, whether it is wired, wireless with a small USB receiver, or Bluetooth, and whether the computer itself still responds.
2. For a wired device, ask them to unplug it, wait a few seconds, and plug it into a different USB port. If it is in a hub or dock, try plugging directly into the computer.
3. For a wireless device, ask them to replace the batteries and check that the switch underneath is on. For a USB receiver, unplug it, wait about ten seconds and plug it back in.
4. For a Bluetooth device, ask them to turn Bluetooth off and on, remove the device and pair it again, or use the Bluetooth article.
5. If it is a laptop touchpad, ask whether a function key or setting turned the touchpad off, and whether an external mouse works.
6. Restart the computer.
7. If they have another computer, ask them to try the device on it. That shows whether the device itself has failed.

## Escalate if
The device fails on another computer too, which means it needs replacing; or the problem is with the computer, such as nothing responding at all. The caller cannot use a keyboard or mouse to follow steps, so a technician may need to call back or arrange remote help.

## Technician notes
From Microsoft's guide: update or reinstall the device driver through Windows Update or Device Manager; if keyboard media keys stop working check that the Human Interface Device Service starts automatically; on some computers a USB keyboard or mouse that stops after a restart or hibernation relates to the BIOS USB legacy support setting.

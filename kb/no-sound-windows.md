---
id: no-sound-windows
title: No sound from speakers or headphones (Windows)
category: hardware
os: windows
keywords: no sound, no audio, speakers, headphones, can't hear, muted, volume, sound not working, audio not working, headset, speaker
sources:
  - https://support.microsoft.com/en-us/windows/hardware/audio/fix-audio-issues-when-no-sound-plays-from-speakers-or-headphones-in-windows
  - https://support.microsoft.com/en-us/windows/hardware/audio/update-audio-drivers-in-windows
drafted: 2026-10-22
reviewed: never
status: approved
license: Internal-Original
attribution: RelayDesk original runbook; paraphrased and safety-scoped from the official source links listed above. No vendor text copied.
reviewed_by: RelayDesk KB editorial curation (2026-10-24)
---
## Steps
1. Ask whether the sound is missing everywhere, or only in one app or one meeting. If it is only one app, check that app's own volume and mute settings.
2. Ask them to check that the volume is up and not muted, on the keyboard, on the speakers, and in the taskbar.
3. Ask them to open Settings, System, Sound, and make sure the right speakers or headphones are selected as the output.
4. Ask whether headphones are plugged in, because speakers may stay silent while headphones are connected. Ask them to unplug and replug the speaker or headset, or try a different USB port for USB devices.
5. For a Bluetooth headset, ask whether it is charged and connected to this computer and not to another device.
6. Restart the computer and test again.

## Escalate if
Still no sound after these steps, the sound device is missing in Settings, or it started after an update. This is probably a driver problem for a technician.

## Technician notes
From Microsoft's guides: run the Audio troubleshooter (Get Help app on Windows 11); set the correct default playback device in the Sound Control Panel; turn off audio enhancements on the device's Enhancements tab; update the audio driver, or uninstall the device in Device Manager and restart so Windows reinstalls it; download the latest driver from the PC maker.

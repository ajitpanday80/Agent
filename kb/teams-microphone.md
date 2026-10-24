---
id: teams-microphone
title: Microphone not working in Microsoft Teams
category: software
os: any
keywords: teams, microphone, mic, can't hear me, no audio, no sound, headset, others can't hear, meeting audio, speaker, zoom audio
sources:
  - https://support.microsoft.com/en-us/teams/meetings/my-microphone-isn-t-working-in-microsoft-teams
  - https://support.microsoft.com/en-us/teams/meetings/my-speaker-isn-t-working-in-microsoft-teams
drafted: 2026-10-22
reviewed: never
status: approved
license: Internal-Original
attribution: RelayDesk original runbook; paraphrased and safety-scoped from the official source links listed above. No vendor text copied.
reviewed_by: RelayDesk KB editorial curation (2026-10-24)
---
## Steps
1. Ask whether the microphone icon in the meeting has a line through it. That means they are muted. Also check for a physical mute switch on the headset.
2. Ask them to choose the right microphone: in the meeting, use the arrow next to the Mic button, or in Teams settings under Devices. Teams also has a test call that plays back a short recording.
3. Check permissions. On Windows, Settings, Privacy and security, Microphone must allow Teams. On a Mac, System Settings, Privacy and Security, Microphone must have Teams ticked. Restart Teams afterward. If Teams runs in a web browser, the browser may ask to allow the microphone.
4. For a Bluetooth headset, check it is charged, connected to this computer, and not connected to another device. Unplug and replug a wired headset.
5. Restart Teams, then the computer.

## Escalate if
The computer does not detect any microphone in its own sound settings, which may need a driver; it only fails on a remote or virtual desktop; or settings appear locked by the company. When the problem is that the caller cannot hear others, the same steps apply to the speaker.

## Technician notes
Reinstall or update the audio driver in Device Manager. For virtual desktop sessions, check the remote audio recording setting and that the Teams VDI app has microphone access on the physical Windows device. Some settings may be locked by an admin policy.

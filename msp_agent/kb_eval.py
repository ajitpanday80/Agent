"""Retrieval test set: what callers actually say -> the article that must be found.
python -m msp_agent.kb_eval   prints accuracy and every miss. Add real phrasings from your call transcripts here."""
from . import kb

# (what the caller says, operating system the agent would pass, expected article id)
CASES = [
    ("my outlook is stuck on loading profile", "windows", "outlook-loading-profile"),
    ("outlook keeps freezing and not responding", "windows", "outlook-loading-profile"),
    ("I can't send emails, they sit in my outbox", "windows", "outlook-send-receive"),
    ("outlook says working offline", "windows", "outlook-send-receive"),
    ("I am not receiving any emails since this morning", "windows", "outlook-send-receive"),
    ("outlook keeps asking for my password", "windows", "outlook-send-receive"),
    ("the printer shows offline", "windows", "printer-offline"),
    ("nothing prints, there is a paper jam", "windows", "printer-offline"),
    ("my mac won't print", "mac", "printer-mac"),
    ("printer is paused on my macbook", "mac", "printer-mac"),
    ("my computer is very slow", "windows", "slow-pc"),
    ("laptop is laggy and takes forever to start", "windows", "slow-pc"),
    ("my mac is really slow", "mac", "mac-slow-or-frozen"),
    ("an app is frozen and I see the spinning wheel", "mac", "mac-slow-or-frozen"),
    ("the wifi is not working", "windows", "wifi-windows"),
    ("connected to wifi but no internet", "windows", "wifi-windows"),
    ("internet keeps dropping on my laptop", "windows", "wifi-windows"),
    ("my macbook can't connect to the wifi", "mac", "wifi-mac"),
    ("vpn won't connect", "windows", "vpn-cannot-connect"),
    ("I get error 809 when I connect to the vpn from home", "windows", "vpn-cannot-connect"),
    ("I forgot my password", "windows", "password-or-lockout"),
    ("my account is locked out", "windows", "password-or-lockout"),
    ("I can't log in to my computer", "windows", "password-or-lockout"),
    ("I got a new phone and can't approve the sign in", "mac", "lost-phone-mfa"),
    ("my authenticator app is gone, lost my phone", "windows", "lost-phone-mfa"),
    ("all our files are encrypted and there is a bitcoin note", "windows", "ransomware-malware"),
    ("we think we have ransomware", "windows", "ransomware-malware"),
    ("I clicked a link in a suspicious email", "windows", "phishing-email"),
    ("I got a phishing email", "windows", "phishing-email"),
    ("I opened an attachment and entered my password on a fake page", "windows", "phishing-email"),
    ("a pop up says my computer is infected and to call microsoft", "windows", "tech-support-scam-popup"),
    ("a warning on my screen says call this number", "windows", "tech-support-scam-popup"),
    ("people can't hear me in teams", "windows", "teams-microphone"),
    ("my microphone is not working in the meeting", "mac", "teams-microphone"),
    ("onedrive is not syncing", "windows", "onedrive-sync"),
    ("onedrive says sync pending for hours", "mac", "onedrive-sync"),
    ("windows update is stuck at 100 percent", "windows", "windows-update-stuck"),
    ("update failed with error 0x80070422", "windows", "windows-update-stuck"),
    ("I got a blue screen and it keeps restarting", "windows", "blue-screen-windows"),
    ("computer crashed with a stop code", "windows", "blue-screen-windows"),
    ("no sound from my speakers", "windows", "no-sound-windows"),
    ("I can't hear anything on my laptop", "windows", "no-sound-windows"),
    ("my bluetooth headset won't connect", "windows", "bluetooth-windows"),
    ("bluetooth is missing", "windows", "bluetooth-windows"),
    ("I closed word without saving my document", "windows", "office-unsaved-file"),
    ("excel crashed and I lost my work", "windows", "office-unsaved-file"),
    ("I deleted a file by mistake", "windows", "restore-deleted-files"),
    ("I need an older version of my document", "windows", "restore-deleted-files"),
    ("my screen is black", "windows", "black-screen-windows"),
    ("the monitor says no signal", "windows", "black-screen-windows"),
    ("my mac won't turn on", "mac", "mac-wont-start"),
    ("macbook is stuck on the apple logo", "mac", "mac-wont-start"),
    ("I can't connect to my office computer with remote desktop", "windows", "remote-desktop-cannot-connect"),
    ("my keyboard stopped working", "windows", "keyboard-mouse-not-working"),
    ("wireless mouse not responding", "windows", "keyboard-mouse-not-working"),
]

# Phrases that must NOT be answered by a guess: they should find nothing (the agent then makes a ticket).
SHOULD_FIND_NOTHING = [
    "what is the weather today",
    "I need to order a new laptop",
    "how do I get a raise",
    "my quickbooks license expired",
]


def evaluate() -> tuple[list[tuple], list[str]]:
    misses = []
    for phrase, osys, expected in CASES:
        got = [a["id"] for a in kb.search(phrase, operating_system=osys)]
        if expected not in got:
            misses.append((phrase, osys, expected, got))
    false_hits = [p for p in SHOULD_FIND_NOTHING if kb.search(p)]
    return misses, false_hits


def main() -> None:
    misses, false_hits = evaluate()
    n = len(CASES)
    print(f"retrieval: {n - len(misses)}/{n} found in top 2")
    for phrase, osys, expected, got in misses:
        print(f"  MISS [{osys}] {phrase!r}: wanted {expected}, got {got}")
    for p in false_hits:
        print(f"  FALSE HIT (should find nothing): {p!r} -> {[a['id'] for a in kb.search(p)]}")


if __name__ == "__main__":
    main()

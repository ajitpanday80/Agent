# MSP L1 Voice Agent

LiveKit Agents + Deepgram (STT/TTS) + OpenRouter (LLM). Mock ticketing and a tiny keyword KB.

## Setup
    python3 -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env   # fill in keys. Never commit .env

## Test without a phone number
- Talk in your terminal (needs a mic, run on your own machine):  `python -m msp_agent.agent console`
- Talk from the browser: `python -m msp_agent.agent dev`, then open https://agents-playground.livekit.io,
  connect to your LiveKit project and join a room.

Tickets created by the agent are written to `tickets.json` (gitignored).

## Next
1. Replace `msp_agent/tickets.py` with your PSA API (ConnectWise/Autotask/Halo).
2. Replace `msp_agent/kb.py` with your real KB.
3. Add a SIP trunk + number (LiveKit SIP or Twilio/Telnyx) and warm transfer in `escalate_to_human`.
4. Add a scenario test suite and review transcripts before any live traffic.

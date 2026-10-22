"""Chat with the agent in text mode (real LLM + real tools, no audio). Useful with no mic or phone.
    python scripts/text_chat.py "Hi, I'm Tom Becker from Acme Dental" "Our printer is offline"
or run with no args for an interactive prompt."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402
from livekit.agents import AgentSession  # noqa: E402
from livekit.agents.voice.run_result import FunctionCallEvent, FunctionCallOutputEvent  # noqa: E402
from livekit.plugins import openai  # noqa: E402

from msp_agent.agent import MspSupportAgent  # noqa: E402

load_dotenv()


def show(result) -> None:
    for ev in result.events:
        if isinstance(ev, FunctionCallEvent):
            print(f"   [tool] {ev.item.name}({ev.item.arguments})")
        elif isinstance(ev, FunctionCallOutputEvent):
            print(f"   [tool result] {ev.item.output}")
        elif getattr(ev.item, "role", None) == "assistant":
            print(f"AGENT: {ev.item.text_content}")


async def main(lines: list[str]) -> None:
    llm = openai.LLM(model=os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5"),
                     base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"])
    async with AgentSession(llm=llm) as session:
        await session.start(MspSupportAgent())
        interactive = not lines
        while True:
            if interactive:
                try:
                    line = input("YOU: ")
                except EOFError:
                    break
            elif lines:
                line = lines.pop(0)
                print(f"YOU: {line}")
            else:
                break
            show(await session.run(user_input=line))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))

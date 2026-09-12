"""Manual CLI for talking to the CookingAgent before any chat UI exists.

Usage:
    export ANTHROPIC_API_KEY=sk-...
    .venv/bin/python scripts/chat_cli.py [vault_dir]

Recipes you approve get written under <vault_dir>/Recipes/.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import CookingAgent  # noqa: E402


def main() -> None:
    vault_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("./vault_dev")
    agent = CookingAgent(vault_dir=vault_dir)
    print(f"ProjectChef CLI - writing approved recipes to {vault_dir.resolve()}")
    print("Type a recipe (or paste a rough one), Ctrl-D to exit.\n")

    while True:
        try:
            message = input("you> ")
        except EOFError:
            break
        if not message.strip():
            continue
        reply = agent.send(message)
        print(f"chef> {reply}\n")


if __name__ == "__main__":
    main()

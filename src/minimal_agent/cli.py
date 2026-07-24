"""Small interactive CLI over the shared AgentService."""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Callable, Sequence
from uuid import uuid4

from minimal_agent.app import build_service
from minimal_agent.config import Settings
from minimal_agent.errors import MinimalAgentError, SessionNotFoundError
from minimal_agent.service import AgentService


InputReader = Callable[[str], str]
OutputWriter = Callable[[str], None]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chat with the Minimal Agent")
    parser.add_argument("--user", default="local-user", help="durable user ID")
    parser.add_argument("--session", help="session ID; defaults to a short UUID")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="show structured INFO-level runtime traces",
    )
    return parser


async def run_interactive(
    service: AgentService,
    *,
    user_id: str,
    session_id: str,
    read_input: InputReader = input,
    write_output: OutputWriter = print,
) -> None:
    """Create or resume one session and run the thin input/output loop."""
    try:
        await service.get_session(user_id, session_id)
    except SessionNotFoundError:
        await service.create_session(user_id, session_id)

    write_output("Minimal Agent")
    write_output(f"Session: {session_id}")
    write_output("")

    while True:
        try:
            user_text = read_input("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            write_output("")
            return

        if user_text in {"/exit", "/quit"}:
            return
        if not user_text:
            continue

        try:
            result = await service.send_message(user_id, session_id, user_text)
        except MinimalAgentError as exc:
            write_output(f"Agent error: {exc}")
            continue
        write_output(f"Agent> {result.final_answer}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.debug else logging.WARNING)
    settings = Settings.from_env()
    if settings.deepseek_api_key is None:
        print("DEEPSEEK_API_KEY is not set.")
        return 2

    session_id = args.session or uuid4().hex[:8]
    try:
        service = build_service(settings)
        asyncio.run(
            run_interactive(
                service,
                user_id=args.user,
                session_id=session_id,
            )
        )
    except MinimalAgentError as exc:
        print(f"Agent error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

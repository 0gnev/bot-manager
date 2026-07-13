"""botmgr command line entry point."""

from __future__ import annotations

import argparse
import sys

from botmgr import __version__
from botmgr.project import ProjectNotFound, find_root
from botmgr.theme import banner, console, fail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="botmgr",
        description="Install, configure and run the Bot Manager Telegram tutor bot.",
    )
    parser.add_argument("--version", action="version", version=f"botmgr {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("setup", help="interactive configuration wizard")
    sub.add_parser("doctor", help="check Docker, tokens, AI provider and vault")
    sub.add_parser("up", help="build and start the bot stack")
    sub.add_parser("down", help="stop the bot stack")
    sub.add_parser("restart", help="restart the bot stack")
    sub.add_parser("status", help="show container status and bridge health")
    logs = sub.add_parser("logs", help="follow stack logs")
    logs.add_argument("--tail", type=int, default=200)
    logs.add_argument("--no-follow", action="store_true")

    args = parser.parse_args(argv)

    if args.command is None:
        banner(f"v{__version__}")
        parser.print_help()
        return 0

    try:
        root = find_root()
    except ProjectNotFound as exc:
        fail(str(exc))
        return 2

    try:
        if args.command == "setup":
            from botmgr import wizard

            return wizard.run(root)
        if args.command == "doctor":
            from botmgr import doctor

            return doctor.run(root)

        from botmgr import stack

        if args.command == "up":
            return stack.up(root)
        if args.command == "down":
            return stack.down(root)
        if args.command == "restart":
            return stack.restart(root)
        if args.command == "status":
            return stack.status(root)
        if args.command == "logs":
            return stack.logs(root, follow=not args.no_follow, tail=args.tail)
    except KeyboardInterrupt:
        console.print("\n[muted]Interrupted.[/muted]")
        return 130

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())

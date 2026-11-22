"""Command line: argument parsing and JSON output. commands: info, list, run, menu.

The engine commands (info, list, run) print JSON only; --json is accepted so
callers can follow the contract in INTERFACE.md. `menu` is an interactive
front-end built on top of those commands.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from typing import Dict, List, Optional

from . import SCHEMA
from .catalog import load_catalog
from .detect import detect_machine
from .runner import make_emitter, make_job, resolve_params, run_queue

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_UNKNOWN_ID, EXIT_CONFIRM, EXIT_JOB_FAILED = 0, 1, 2, 3, 4, 5


class UsageError(Exception):
    pass


def ok(data, pretty: bool = False) -> int:
    print(json.dumps({"schema": SCHEMA, "ok": True, "data": data},
                     ensure_ascii=False, indent=2 if pretty else None))
    return EXIT_OK


def fail(code: str, message: str, exit_code: int, details=None) -> int:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    print(json.dumps({"schema": SCHEMA, "ok": False, "error": error}, ensure_ascii=False))
    print("tacman: %s" % message, file=sys.stderr)
    return exit_code


class JsonArgumentParser(argparse.ArgumentParser):
    """Usage errors become a JSON error object (exit code 2)."""

    def error(self, message):
        fail("usage", message, EXIT_USAGE)
        sys.exit(EXIT_USAGE)


def parse_params(items: List[str]) -> Dict[str, str]:
    supplied: Dict[str, str] = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise UsageError("--param expects name=value or id.name=value, got %r" % item)
        supplied[key] = value
    return supplied


def cmd_info(args) -> int:
    return ok(detect_machine(check_sudo=True).to_dict(), pretty=args.pretty)


def cmd_list(args) -> int:
    entries = load_catalog(detect_machine())
    if args.category:
        entries = [e for e in entries if e.category.lower() == args.category.lower()]
    if args.search:
        word = args.search.lower()
        entries = [e for e in entries
                   if word in (e.id + " " + e.name + " " + e.description).lower()]
    return ok({"entries": [e.to_dict() for e in entries]}, pretty=args.pretty)


def cmd_run(args) -> int:
    machine = detect_machine()
    by_id = {e.id: e for e in load_catalog(machine)}

    unknown = [i for i in args.ids if i not in by_id]
    if unknown:
        return fail("unknown_id", "No entry with id " + ", ".join(repr(i) for i in unknown),
                    EXIT_UNKNOWN_ID, {"ids": unknown})

    supplied = parse_params(args.param)
    values_by_id: Dict[str, Dict[str, str]] = {}
    missing = []
    for entry_id in dict.fromkeys(args.ids):
        values, absent = resolve_params(by_id[entry_id], supplied)
        values_by_id[entry_id] = values
        missing += [{"id": entry_id, "name": name} for name in absent]
    if missing:
        names = ", ".join("%s.%s" % (m["id"], m["name"]) for m in missing)
        return fail("missing_param", "Missing required param(s): " + names, EXIT_USAGE, {"params": missing})

    if not args.dry_run:
        approved = {s.strip() for s in (args.confirm_risky or "").split(",") if s.strip()}
        unapproved = [i for i in dict.fromkeys(args.ids)
                      if by_id[i].risky and "all" not in approved and i not in approved]
        if unapproved:
            return fail("confirmation_required",
                        "Risky entries need --confirm-risky: " + ", ".join(unapproved),
                        EXIT_CONFIRM, {"ids": unapproved})

    jobs = [make_job(by_id[i], values_by_id[i], machine) for i in args.ids]
    return run_queue(jobs, make_emitter(sys.stdout),
                     on_failure=args.on_failure, dry_run=args.dry_run)


def cmd_menu(args) -> int:
    from .menu import main as menu_main
    return menu_main()


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="tacman", description="Curated Linux command catalog (JSON interface).")
    sub = parser.add_subparsers(dest="command", required=False)

    def add(name, func, help_text):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--json", action="store_true", help="accepted for contract compatibility (always on)")
        p.set_defaults(func=func)
        return p

    p = add("info", cmd_info, "show detected distro and package manager")
    p.add_argument("--pretty", action="store_true", help="indent the JSON (for humans)")

    p = add("list", cmd_list, "list catalog entries that work on this machine")
    p.add_argument("--category")
    p.add_argument("--search")
    p.add_argument("--pretty", action="store_true", help="indent the JSON (for humans)")

    p = add("run", cmd_run, "run entries one at a time, streaming JSON Lines events")
    p.add_argument("ids", nargs="+", metavar="id")
    p.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
    p.add_argument("--confirm-risky", metavar="IDS", help="comma-separated ids, or 'all'")
    p.add_argument("--on-failure", choices=["continue", "abort"], default="abort")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("menu", help="interactive menu: pick commands by number or keyword")
    p.set_defaults(func=cmd_menu)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:  # bare `tacman`: menu in a terminal, usage error in a pipe
        if not _interactive():
            parser.error("a command is required: info, list, run or menu")
        args.func = cmd_menu
    try:
        return args.func(args)
    except UsageError as exc:
        return fail("usage", str(exc), EXIT_USAGE)
    except BrokenPipeError:
        return EXIT_ERROR
    except Exception as exc:  # last resort: still honour the JSON contract
        traceback.print_exc(file=sys.stderr)
        return fail("internal", "%s: %s" % (type(exc).__name__, exc), EXIT_ERROR)

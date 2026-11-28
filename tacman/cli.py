"""Command line: argument parsing and JSON output. commands: info, list, plan, run, custom, menu.

The engine commands (everything except menu) print JSON only; --json is accepted so
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
from .catalog import CatalogError, add_custom, delete_custom, describe_custom, edit_custom, load_catalog
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


# Exit codes for catalog errors; anything not listed is a usage-type error (2).
ERROR_EXIT = {"unknown_id": EXIT_UNKNOWN_ID, "catalog_unreadable": EXIT_ERROR, "internal": EXIT_ERROR}


def catalog_fail(exc: CatalogError) -> int:
    return fail(exc.code, str(exc), ERROR_EXIT.get(exc.code, EXIT_USAGE))


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


def cmd_plan(args) -> int:
    """Dry run: what would execute, in order. Nothing is run or changed, and
    missing params are reported (not an error) so a front-end can ask for them."""
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

    jobs = []
    for entry_id in args.ids:
        job = make_job(by_id[entry_id], values_by_id[entry_id], machine)
        jobs.append({"id": entry_id, "command": job.command, "argv": job.argv,
                     "sudo": job.entry.sudo, "risky": job.entry.risky})
    needs_confirmation = [i for i in dict.fromkeys(args.ids) if by_id[i].risky]
    return ok({"jobs": jobs, "needs_confirmation": needs_confirmation, "missing_params": missing})


def cmd_custom_add(args) -> int:
    try:
        stored = add_custom(
            name=args.name, command=args.command, description=args.description,
            category=args.category, sudo=args.sudo, risky=args.risky, entry_id=args.id)
    except CatalogError as exc:
        return catalog_fail(exc)
    return ok(describe_custom(stored, detect_machine()))


def cmd_custom_edit(args) -> int:
    changes = {key: getattr(args, key) for key in
               ("name", "description", "category", "command", "sudo", "risky")
               if getattr(args, key) is not None}
    if not changes:
        raise UsageError("nothing to change: give at least one of --name, --description, "
                         "--category, --command, --sudo/--no-sudo, --risky/--no-risky")
    try:
        stored = edit_custom(args.id, changes)
    except CatalogError as exc:
        return catalog_fail(exc)
    return ok(describe_custom(stored, detect_machine()))


def cmd_custom_delete(args) -> int:
    try:
        removed = delete_custom(args.id)
    except CatalogError as exc:
        return catalog_fail(exc)
    return ok({"id": args.id, "name": removed.get("name"), "deleted": True})


def cmd_menu(args) -> int:
    from .menu import main as menu_main
    return menu_main()


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="tacman", description="Curated Linux command catalog (JSON interface).")
    # dest must not be "command": `custom add/edit --command` would overwrite it.
    sub = parser.add_subparsers(dest="subcommand", required=False)

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

    p = add("plan", cmd_plan, "show exactly what would run, without running anything")
    p.add_argument("ids", nargs="+", metavar="id")
    p.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")

    custom = sub.add_parser("custom", help="add, edit or delete your own catalog entries")
    custom_sub = custom.add_subparsers(dest="custom_command", required=True)

    def add_custom_cmd(name, func, help_text):
        cp = custom_sub.add_parser(name, help=help_text)
        cp.add_argument("--json", action="store_true", help="accepted for contract compatibility (always on)")
        cp.set_defaults(func=func)
        return cp

    cp = add_custom_cmd("add", cmd_custom_add, "add an entry of your own")
    cp.add_argument("--name", required=True)
    cp.add_argument("--command", required=True)
    cp.add_argument("--description", default="")
    cp.add_argument("--category", default="Custom")
    cp.add_argument("--id", help="choose the id yourself (default: made from the name)")
    cp.add_argument("--sudo", action="store_true")
    cp.add_argument("--risky", action="store_true")

    cp = add_custom_cmd("edit", cmd_custom_edit, "change one of your own entries")
    cp.add_argument("id")
    for option in ("name", "description", "category", "command"):
        cp.add_argument("--" + option)
    for flag in ("sudo", "risky"):
        group = cp.add_mutually_exclusive_group()
        group.add_argument("--" + flag, dest=flag, action="store_const", const=True)
        group.add_argument("--no-" + flag, dest=flag, action="store_const", const=False)

    cp = add_custom_cmd("delete", cmd_custom_delete, "delete one of your own entries")
    cp.add_argument("id")

    p = sub.add_parser("menu", help="interactive menu: pick commands by number or keyword")
    p.set_defaults(func=cmd_menu)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.subcommand is None:  # bare `tacman`: menu in a terminal, usage error in a pipe
        if not _interactive():
            parser.error("a command is required: info, list, plan, run, custom or menu")
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

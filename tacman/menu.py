"""Interactive menu front-end for the tacman engine.

This is deliberately just another client: it talks to the engine only through
the JSON interface (see INTERFACE.md), exactly like a front-end written in
another language would. Start it with `python3 -m tacman menu`.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Dict, List, Optional, Set, Tuple

ENGINE = [sys.executable, "-m", "tacman"]
RANGE = re.compile(r"(\d+)-(\d+)")

Entry = Dict[str, object]


class EngineError(Exception):
    pass


def call(*args: str) -> Dict:
    """Run a one-shot engine command and return the `data` of its answer."""
    proc = subprocess.run(ENGINE + list(args), stdout=subprocess.PIPE,
                          text=True, encoding="utf-8")
    try:
        doc = json.loads(proc.stdout)
    except ValueError:
        raise EngineError("the engine returned no JSON (exit code %d)" % proc.returncode)
    if not doc.get("ok"):
        raise EngineError(doc.get("error", {}).get("message", "unknown engine error"))
    return doc["data"]


# ---------------------------------------------------------------- picking
def print_menu(entries: List[Entry]) -> None:
    category = None
    for number, e in enumerate(entries, 1):
        if e["category"] != category:
            category = e["category"]
            print("\n== %s ==" % category)
        flags = ("[sudo] " if e["sudo"] else "") + ("[RISKY] " if e["risky"] else "")
        print("  %3d. %-26s %-15s%s" % (number, e["name"], flags, e["description"]))


def parse_selection(text: str, entries: List[Entry]) -> List[int]:
    """'1 4 6-8', a keyword like 'disk', or a mix -> unique 1-based numbers in
    the order typed. Out-of-range numbers are ignored; ranges are clamped."""
    count = len(entries)
    picked: List[int] = []
    seen: Set[int] = set()

    def add(number: int) -> None:
        if 1 <= number <= count and number not in seen:
            picked.append(number)
            seen.add(number)

    for token in text.split():
        match = RANGE.fullmatch(token)
        if match:
            a, b = int(match.group(1)), int(match.group(2))
            if max(a, b) < 1 or min(a, b) > count:
                continue  # entirely outside the menu
            a, b = min(max(a, 1), count), min(max(b, 1), count)
            step = 1 if a <= b else -1
            for number in range(a, b + step, step):
                add(number)
        elif token.isdecimal():
            add(int(token))
        else:
            word = token.lower()
            for number, e in enumerate(entries, 1):
                haystack = ("%s %s %s" % (e["id"], e["name"], e["description"])).lower()
                if number not in seen and word in haystack:
                    add(number)
    return picked


# ---------------------------------------------------------------- queue
def show_queue(queue: List[Entry]) -> None:
    print("\n---- Run queue ----")
    for position, e in enumerate(queue, 1):
        print("  %d. %s  ->  %s%s" % (position, e["name"], "sudo " if e["sudo"] else "", e["command"]))


def review_queue(queue: List[Entry]) -> Optional[List[Entry]]:
    """Let the user reorder or remove jobs. Returns None to go back."""
    queue = list(queue)
    while True:
        show_queue(queue)
        print("\nCommands: 'run' to execute | 'move <from> <to>' | 'del <n>' | 'quit' (back to menu)")
        parts = input("queue> ").split()
        if not parts:
            continue
        command = parts[0]
        if command in ("quit", "q"):
            return None
        if command == "run":
            return queue
        try:
            if command == "del" and len(parts) == 2:
                n = int(parts[1])
                if not 1 <= n <= len(queue):
                    raise IndexError
                del queue[n - 1]
            elif command == "move" and len(parts) == 3:
                a, b = int(parts[1]), int(parts[2])
                if not (1 <= a <= len(queue) and 1 <= b <= len(queue)):
                    raise IndexError
                queue.insert(b - 1, queue.pop(a - 1))
            else:
                print("unrecognised queue command")
                continue
        except (ValueError, IndexError):
            print("bad index")
            continue
        if not queue:
            print("queue is empty")
            return None


def collect_params(queue: List[Entry]) -> Dict[str, str]:
    """Ask for each entry's parameters. Keys are 'id.name' so two entries that
    need the same param name (install + remove) don't clash."""
    params: Dict[str, str] = {}
    asked: Set[str] = set()
    for e in queue:
        if e["id"] in asked:
            continue
        asked.add(e["id"])
        for p in e["params"]:
            while True:
                value = input("  %s - %s: " % (e["name"], p["prompt"])).strip()
                if value or not p.get("required", True):
                    break
                print("  this one is required")
            if value:
                params["%s.%s" % (e["id"], p["name"])] = value
    return params


def confirm_risky(queue: List[Entry]) -> Tuple[List[Entry], Set[str]]:
    """Make the user type 'yes' for each risky job; declined jobs are dropped."""
    approved: Set[str] = set()
    kept: List[Entry] = []
    for e in queue:
        if not e["risky"] or e["id"] in approved:
            kept.append(e)
            continue
        print("\n[!] RISKY: %s -> %s" % (e["name"], e["command"]))
        if input("    Type 'yes' to run: ").strip() == "yes":
            approved.add(e["id"])
            kept.append(e)
        else:
            print("    skipped by user")
    return kept, approved


def ensure_sudo(info: Dict, queue: List[Entry]) -> None:
    """The engine uses `sudo -n` (never prompts), so offer to cache the
    password up front instead of failing halfway through the queue."""
    if info.get("sudo_cached") or not any(e["sudo"] for e in queue):
        return
    print("\nSome jobs need sudo and your password isn't cached.")
    if input("Run 'sudo -v' now to enter it? [Y/n]: ").strip().lower() in ("", "y", "yes"):
        if subprocess.run(["sudo", "-v"]).returncode != 0:
            print("sudo did not succeed; jobs that need it will fail.")


# ---------------------------------------------------------------- running
def run_job(entry: Entry, params: Dict[str, str], approved: bool) -> Dict:
    """Run one entry through the engine, echoing its output live.
    Returns {"status", "exit_code", "reason"} from the engine's job_end event."""
    args = ["run", entry["id"], "--json"]
    prefix = entry["id"] + "."
    for key, value in params.items():
        if key.startswith(prefix):
            args += ["--param", "%s=%s" % (key, value)]
    if approved:
        args += ["--confirm-risky", entry["id"]]

    result = {"status": "failed", "exit_code": None, "reason": "engine_error"}
    proc = subprocess.Popen(ENGINE + args, stdout=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            try:
                doc = json.loads(line)
            except ValueError:
                doc = None
            if not isinstance(doc, dict):
                print("  | " + line)
                continue
            event = doc.get("event")
            if event == "output":
                marker = "!" if doc.get("stream") == "stderr" else "|"
                print("  %s %s" % (marker, doc.get("text", "")))
            elif event == "job_end":
                result = {k: doc.get(k) for k in ("status", "exit_code", "reason")}
            elif doc.get("ok") is False:
                result = {"status": "failed", "exit_code": None,
                          "reason": doc.get("error", {}).get("message", "engine error")}
        proc.wait()
    except KeyboardInterrupt:
        proc.wait()
        result = {"status": "failed", "exit_code": None, "reason": "interrupted"}
    finally:
        proc.stdout.close()
    return result


def run_queue_interactive(queue: List[Entry], params: Dict[str, str], approved: Set[str]) -> None:
    """Run jobs strictly one at a time; after a failure, ask what to do."""
    line = "=" * 60
    ok = failed = 0
    for position, e in enumerate(queue, 1):
        print("\n%s\n[%d/%d] %s\n%s" % (line, position, len(queue), e["name"], line))
        result = run_job(e, params, e["id"] in approved)
        if result.get("status") == "ok":
            ok += 1
            print("\nOK: %s" % e["name"])
            continue
        failed += 1
        reason = result.get("reason")
        detail = "exit %s" % result["exit_code"] if result.get("exit_code") is not None else reason
        print("\nFAILED: %s (%s)" % (e["name"], detail))
        if reason == "sudo_password_required":
            print("  hint: run 'sudo -v' to cache your password, then try again.")
        if reason == "interrupted":
            print("queue interrupted.")
            break
        if position < len(queue) and input("[c]ontinue / [q]uit queue: ").strip().lower() == "q":
            print("queue aborted.")
            break
    not_run = len(queue) - ok - failed
    print("\nDone: %d ok, %d failed%s." % (ok, failed, ", %d not run" % not_run if not_run else ""))


# ---------------------------------------------------------------- your own commands
def add_custom_entry() -> bool:
    """Ask for the details of a new command and save it through the engine."""
    print("\nAdd your own command (leave the name empty to cancel).")
    name = input("  Name: ").strip()
    if not name:
        print("  cancelled")
        return False
    description = input("  Description: ").strip()
    category = input("  Category [Custom]: ").strip() or "Custom"
    command = input("  Command: ").strip()
    if not command:
        print("  a command is required; cancelled")
        return False
    sudo = input("  Needs sudo? [y/N]: ").strip().lower() in ("y", "yes")
    risky = input("  Risky or destructive? [y/N]: ").strip().lower() in ("y", "yes")

    # --opt=value form, so values that start with '-' aren't taken for options
    args = ["custom", "add", "--json", "--name=" + name, "--description=" + description,
            "--category=" + category, "--command=" + command]
    if sudo:
        args.append("--sudo")
    if risky:
        args.append("--risky")
    try:
        entry = call(*args)
    except EngineError as exc:
        print("  could not add it: %s" % exc)
        return False
    print("  added '%s' (id: %s)" % (entry["name"], entry["id"]))
    return True


def delete_custom_entry(entries: List[Entry]) -> bool:
    """Let the user delete one of the commands they added themselves."""
    mine = [e for e in entries if e.get("source") == "custom"]
    if not mine:
        print("\nYou haven't added any commands of your own yet.")
        return False
    print("\nYour own commands:")
    for number, e in enumerate(mine, 1):
        print("  %d. %s  ->  %s" % (number, e["name"], e["command"]))
    choice = input("Delete which one? (number, or empty to cancel): ").strip()
    if not choice:
        return False
    if not choice.isdecimal() or not 1 <= int(choice) <= len(mine):
        print("bad number")
        return False
    entry = mine[int(choice) - 1]
    if input("  Type 'yes' to delete '%s': " % entry["name"]).strip() != "yes":
        print("  kept")
        return False
    try:
        call("custom", "delete", entry["id"], "--json")
    except EngineError as exc:
        print("  could not delete it: %s" % exc)
        return False
    print("  deleted")
    return True


def reload_entries(entries: List[Entry]) -> List[Entry]:
    try:
        return call("list")["entries"]
    except EngineError as exc:
        print("could not refresh the menu: %s" % exc)
        return entries


# ---------------------------------------------------------------- main
def main() -> int:
    try:
        info = call("info")
        entries = call("list")["entries"]
    except EngineError as exc:
        print("tacman: %s" % exc, file=sys.stderr)
        return 1

    like = ("  |  like: " + " ".join(info["id_like"])) if info["id_like"] else ""
    print("Distro: %s (%s)%s" % (info["distro_name"], info["distro_id"], like))
    if not info["supported"]:
        print("Note: this distro isn't supported yet (v1 covers Debian and Arch families);\n"
              "package entries are hidden.")
    if not entries:
        print("No catalog entries apply to this machine.")
        return 1

    try:
        while True:
            print_menu(entries)
            print("\nPick: numbers & ranges (1 4 6-8) or a keyword (disk)")
            print("      'add' = add your own command | 'delete' = remove one of yours | 'q' = quit")
            selection = input("select> ").strip()
            if selection in ("q", "quit"):
                return 0
            if selection == "add":
                if add_custom_entry():
                    entries = reload_entries(entries)
                continue
            if selection == "delete":
                if delete_custom_entry(entries):
                    entries = reload_entries(entries)
                continue
            numbers = parse_selection(selection, entries)
            if not numbers:
                print("nothing matched")
                continue
            queue = review_queue([entries[n - 1] for n in numbers])
            if queue is None:
                continue
            params = collect_params(queue)
            queue, approved = confirm_risky(queue)
            if not queue:
                print("nothing left to run.")
                continue
            ensure_sudo(info, queue)
            run_queue_interactive(queue, params, approved)
            if input("\nPress Enter to return to the menu, or q to quit: ").strip().lower() in ("q", "quit"):
                return 0
    except (KeyboardInterrupt, EOFError):
        print("\nbye.")
        return 0

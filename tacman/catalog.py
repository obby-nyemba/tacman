"""Catalog loading: builtin entries + the user's custom entries, resolved
for this machine.

An entry has either one universal `command`, or a `commands` object keyed by
package manager (v1: "apt", "pacman"). Entries with no variant for this
machine are hidden.
"""
from __future__ import annotations

import json
import os
import pkgutil
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

from .detect import Machine

CATEGORY_ORDER = ["System info", "Packages", "Disk", "Network", "Users", "Services", "Misc"]
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
NAME_RE = re.compile(r"^\w+$")


@dataclass
class Entry:
    id: str
    name: str
    description: str
    category: str
    command: str  # resolved for this machine; may contain {param} placeholders
    sudo: bool = False
    risky: bool = False
    params: List[Dict[str, object]] = field(default_factory=list)
    source: str = "builtin"

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def custom_catalog_path() -> str:
    """~/.config/tacman/catalog.json (override with $TACMAN_CATALOG)."""
    override = os.environ.get("TACMAN_CATALOG")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".config", "tacman", "catalog.json")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValueError("cannot make an id from name %r" % name)
    return slug


def _normalize(raw: object) -> Dict[str, object]:
    """Validate one raw JSON entry and fill in defaults. Raises ValueError."""
    if not isinstance(raw, dict):
        raise ValueError("entry must be a JSON object")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("missing 'name'")

    command = raw.get("command")
    commands = raw.get("commands")
    has_command = isinstance(command, str) and bool(command.strip())
    has_commands = (
        isinstance(commands, dict) and bool(commands)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in commands.items())
    )
    if has_command == has_commands:
        raise ValueError("%r needs exactly one of 'command' or 'commands'" % name)

    entry_id = raw.get("id") or slugify(name)
    if not isinstance(entry_id, str) or not ID_RE.match(entry_id):
        raise ValueError("bad id %r (use a-z, 0-9 and '-')" % (entry_id,))

    params = []
    for p in raw.get("params") or []:
        if not isinstance(p, dict) or not NAME_RE.match(str(p.get("name", ""))):
            raise ValueError("%r has a bad param definition" % name)
        params.append({
            "name": p["name"],
            "prompt": str(p.get("prompt", p["name"])),
            "required": bool(p.get("required", True)),
        })

    return {
        "id": entry_id,
        "name": name.strip(),
        "description": str(raw.get("description", "")),
        "category": str(raw.get("category") or "Misc"),
        "command": command if has_command else None,
        "commands": commands if has_commands else None,
        "sudo": bool(raw.get("sudo", False)),
        "risky": bool(raw.get("risky", False)),
        "params": params,
    }


def resolve_command(norm: Dict[str, object], machine: Machine) -> Optional[str]:
    """Command string for this machine, or None when the entry doesn't apply."""
    commands = norm.get("commands")
    if commands:
        return commands.get(machine.pkg_manager) if machine.pkg_manager else None
    return norm["command"]


def _warn_stderr(message: str) -> None:
    print("tacman: warning: %s" % message, file=sys.stderr)


def _load_builtin_raw() -> List[object]:
    data = pkgutil.get_data("tacman", "data/builtin.json")
    return json.loads(data.decode("utf-8"))


def _menu_key(entry: Entry):
    if entry.category in CATEGORY_ORDER:
        return (CATEGORY_ORDER.index(entry.category), "")
    return (len(CATEGORY_ORDER), entry.category.lower())


def load_catalog(
    machine: Machine,
    custom_path: Optional[str] = None,
    warn: Optional[Callable[[str], None]] = None,
) -> List[Entry]:
    """Builtin + custom entries that work on `machine`, in menu order.

    A broken builtin file is a bug and raises. A broken custom entry is
    skipped with a warning so one typo never takes the whole tool down.
    """
    warn = warn or _warn_stderr
    candidates = [(raw, "builtin") for raw in _load_builtin_raw()]

    path = custom_path or custom_catalog_path()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                custom = json.load(handle)
            if not isinstance(custom, list):
                raise ValueError("top level must be a JSON array")
            candidates += [(raw, "custom") for raw in custom]
        except (OSError, ValueError) as exc:  # JSONDecodeError is a ValueError
            warn("custom catalog %s unreadable: %s" % (path, exc))

    entries: List[Entry] = []
    seen = set()
    for raw, source in candidates:
        try:
            norm = _normalize(raw)
            if norm["id"] in seen:
                raise ValueError("duplicate id %r" % norm["id"])
        except ValueError as exc:
            if source == "builtin":
                raise
            warn("skipping custom entry: %s" % exc)
            continue
        seen.add(norm["id"])  # reserved even if hidden on this machine

        command = resolve_command(norm, machine)
        if command is None:
            continue
        entries.append(Entry(
            id=norm["id"], name=norm["name"], description=norm["description"],
            category=norm["category"], command=command, sudo=norm["sudo"],
            risky=norm["risky"], params=norm["params"], source=source,
        ))

    entries.sort(key=_menu_key)  # stable: file order kept inside a category
    return entries

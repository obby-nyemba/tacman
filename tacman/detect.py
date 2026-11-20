"""Machine detection: which distro family are we on, and which package manager?

v1 supports two families: Debian (apt) and Arch (pacman). Anything else is
reported as unsupported; the engine still works, but only universal catalog
entries (a single `command`) are shown.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import __version__

OS_RELEASE_PATH = "/etc/os-release"

# Distro ID (or an ID_LIKE word) -> family.
FAMILY_BY_ID = {
    "debian": "debian", "ubuntu": "debian", "linuxmint": "debian",
    "pop": "debian", "raspbian": "debian", "kali": "debian",
    "elementary": "debian", "zorin": "debian", "devuan": "debian",
    "arch": "arch", "manjaro": "arch", "endeavouros": "arch",
    "archarm": "arch", "cachyos": "arch", "garuda": "arch",
}
PKG_MANAGER_BY_FAMILY = {"debian": "apt", "arch": "pacman"}
FAMILY_BY_PKG_MANAGER = {pm: fam for fam, pm in PKG_MANAGER_BY_FAMILY.items()}


@dataclass
class Machine:
    distro_id: str = "unknown"
    distro_name: str = "Unknown Linux"
    id_like: List[str] = field(default_factory=list)
    family: Optional[str] = None       # "debian", "arch" or None
    pkg_manager: Optional[str] = None  # "apt", "pacman" or None
    is_root: bool = False
    sudo_cached: Optional[bool] = None  # None = not checked

    @property
    def supported(self) -> bool:
        return self.family is not None

    def to_dict(self) -> Dict[str, object]:
        return {
            "engine_version": __version__,
            "distro_id": self.distro_id,
            "distro_name": self.distro_name,
            "id_like": list(self.id_like),
            "pkg_manager": self.pkg_manager,
            "family": self.family,
            "supported": self.supported,
            "is_root": self.is_root,
            # True when privileged jobs can run right now without a password.
            "sudo_cached": self.is_root or bool(self.sudo_cached),
        }


def parse_os_release(text: str) -> Dict[str, str]:
    """Parse KEY=value lines of /etc/os-release (values may be quoted)."""
    info: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        info[key.strip()] = value
    return info


def _read_os_release() -> str:
    try:
        with open(OS_RELEASE_PATH, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def sudo_is_cached(which: Callable[[str], Optional[str]] = shutil.which) -> bool:
    """True if `sudo -n` works right now (credentials cached or NOPASSWD)."""
    if not which("sudo"):
        return False
    try:
        result = subprocess.run(
            ["sudo", "-n", "true"], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def detect_machine(
    os_release_text: Optional[str] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
    is_root: Optional[bool] = None,
    check_sudo: bool = False,
) -> Machine:
    """Work out distro, family and package manager.

    Order: distro ID, then each ID_LIKE word, then whichever package manager
    binary exists. Arguments exist so tests can inject fake machines.
    """
    info = parse_os_release(_read_os_release() if os_release_text is None else os_release_text)
    distro_id = info.get("ID", "").lower() or "unknown"
    id_like = [word.lower() for word in info.get("ID_LIKE", "").split()]

    family = None
    for token in [distro_id] + id_like:
        if token in FAMILY_BY_ID:
            family = FAMILY_BY_ID[token]
            break
    if family is None:
        for pm, fam in FAMILY_BY_PKG_MANAGER.items():
            if which(pm):
                family = fam
                break

    if is_root is None:
        is_root = hasattr(os, "geteuid") and os.geteuid() == 0

    return Machine(
        distro_id=distro_id,
        distro_name=info.get("NAME", "") or "Unknown Linux",
        id_like=id_like,
        family=family,
        pkg_manager=PKG_MANAGER_BY_FAMILY.get(family) if family else None,
        is_root=is_root,
        sudo_cached=sudo_is_cached(which) if check_sudo and not is_root else None,
    )

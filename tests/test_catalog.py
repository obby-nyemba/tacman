import json
import os
import tempfile
import unittest

from tacman.catalog import _load_builtin_raw, _normalize, load_catalog, resolve_command, slugify
from tacman.detect import Machine

DEBIAN = Machine(distro_id="debian", family="debian", pkg_manager="apt")
ARCH = Machine(distro_id="arch", family="arch", pkg_manager="pacman")
OTHER = Machine(distro_id="fedora")
NO_CUSTOM = "/nonexistent/catalog.json"


def ids(entries):
    return {e.id for e in entries}


class CatalogTests(unittest.TestCase):
    def test_universal_command(self):
        norm = _normalize({"name": "Disk", "command": "df -h"})
        self.assertEqual(resolve_command(norm, DEBIAN), "df -h")
        self.assertEqual(resolve_command(norm, OTHER), "df -h")

    def test_package_manager_variants(self):
        norm = _normalize({"name": "Upd", "commands": {"apt": "A", "pacman": "P"}})
        self.assertEqual(resolve_command(norm, DEBIAN), "A")
        self.assertEqual(resolve_command(norm, ARCH), "P")
        self.assertIsNone(resolve_command(norm, OTHER))

    def test_entry_needs_exactly_one_command_form(self):
        with self.assertRaises(ValueError):
            _normalize({"name": "x"})
        with self.assertRaises(ValueError):
            _normalize({"name": "x", "command": "a", "commands": {"apt": "b"}})

    def test_slugify_and_generated_id(self):
        self.assertEqual(slugify("Docker containers!"), "docker-containers")
        self.assertEqual(_normalize({"name": "My Cmd", "command": "true"})["id"], "my-cmd")

    def test_builtin_is_valid_and_ids_unique(self):
        raws = [_normalize(r) for r in _load_builtin_raw()]
        all_ids = [r["id"] for r in raws]
        self.assertEqual(len(all_ids), len(set(all_ids)))

    def test_every_package_entry_covers_both_v1_families(self):
        for raw in _load_builtin_raw():
            if "commands" in raw:
                self.assertEqual(set(raw["commands"]), {"apt", "pacman"}, raw["id"])

    def test_builtin_loads_on_both_families(self):
        for machine, expected in ((DEBIAN, "apt update && apt upgrade -y"), (ARCH, "pacman -Syu --noconfirm")):
            entries = {e.id: e for e in load_catalog(machine, NO_CUSTOM)}
            self.assertEqual(entries["update-system"].command, expected)
            self.assertTrue(entries["update-system"].sudo)
            self.assertTrue(entries["remove-package"].risky)

    def test_unsupported_machine_keeps_only_universal_entries(self):
        got = ids(load_catalog(OTHER, NO_CUSTOM))
        self.assertIn("disk-usage", got)
        self.assertNotIn("update-system", got)

    def test_menu_order_follows_categories(self):
        cats = [e.category for e in load_catalog(DEBIAN, NO_CUSTOM)]
        self.assertEqual(cats[0], "System info")
        self.assertEqual(cats[-1], "Services")

    def test_custom_entries_merge_and_bad_ones_are_skipped(self):
        custom = [
            {"name": "Docker containers", "category": "Docker", "command": "docker ps"},
            {"name": "broken"},                                   # no command
            {"id": "disk-usage", "name": "Dup", "command": "x"},  # clashes with builtin
        ]
        warnings = []
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "catalog.json")
            with open(path, "w") as f:
                json.dump(custom, f)
            entries = load_catalog(DEBIAN, path, warn=warnings.append)
        by_id = {e.id: e for e in entries}
        self.assertEqual(by_id["docker-containers"].source, "custom")
        self.assertEqual(by_id["disk-usage"].source, "builtin")
        self.assertEqual(len(warnings), 2)
        self.assertEqual(entries[-1].category, "Docker")  # custom categories go last

    def test_unreadable_custom_file_only_warns(self):
        warnings = []
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "catalog.json")
            with open(path, "w") as f:
                f.write("{not json")
            entries = load_catalog(DEBIAN, path, warn=warnings.append)
        self.assertTrue(entries)
        self.assertEqual(len(warnings), 1)


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import unittest

from tacman.catalog import (CatalogError, add_custom, delete_custom, describe_custom,
                            edit_custom, read_custom_raw)
from tacman.detect import Machine

DEBIAN = Machine(distro_id="debian", family="debian", pkg_manager="apt")
OTHER = Machine(distro_id="fedora")


class CustomCatalogTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.path = os.path.join(tmp.name, "sub", "catalog.json")  # folder doesn't exist yet

    def stored(self):
        with open(self.path, encoding="utf-8") as handle:
            return json.load(handle)

    def error_code(self, func, *args, **kwargs):
        with self.assertRaises(CatalogError) as ctx:
            func(*args, **kwargs)
        return ctx.exception.code

    # ---- add
    def test_add_creates_folder_and_file_with_defaults(self):
        entry = add_custom("Docker containers", "docker ps", path=self.path)
        self.assertEqual(entry["id"], "docker-containers")
        self.assertEqual((entry["category"], entry["sudo"], entry["risky"]), ("Custom", False, False))
        self.assertEqual(self.stored(), [entry])
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["catalog.json"])  # no .tmp left over

    def test_same_name_gets_a_numbered_id(self):
        add_custom("Say hi", "echo 1", path=self.path)
        self.assertEqual(add_custom("Say hi", "echo 2", path=self.path)["id"], "say-hi-2")
        self.assertEqual(add_custom("Say hi", "echo 3", path=self.path)["id"], "say-hi-3")

    def test_generated_id_never_reuses_a_builtin_id(self):
        self.assertEqual(add_custom("Disk usage", "df", path=self.path)["id"], "disk-usage-2")

    def test_explicit_id_must_be_free(self):
        self.assertEqual(self.error_code(add_custom, "X", "true", entry_id="disk-usage", path=self.path),
                         "invalid_entry")
        add_custom("Mine", "true", entry_id="mine", path=self.path)
        self.assertEqual(self.error_code(add_custom, "Other", "true", entry_id="mine", path=self.path),
                         "invalid_entry")

    def test_invalid_entries_are_rejected_and_nothing_is_written(self):
        self.assertEqual(self.error_code(add_custom, "!!!", "true", path=self.path), "invalid_entry")
        self.assertEqual(self.error_code(add_custom, "Empty", "", path=self.path), "invalid_entry")
        self.assertEqual(self.error_code(add_custom, "Bad id", "true", entry_id="Bad Id!", path=self.path),
                         "invalid_entry")
        self.assertFalse(os.path.exists(self.path))

    # ---- edit
    def test_edit_changes_fields_but_never_the_id(self):
        add_custom("Say hi", "echo hi", path=self.path)
        edited = edit_custom("say-hi", {"name": "Say hello", "sudo": True}, path=self.path)
        self.assertEqual((edited["id"], edited["name"], edited["command"], edited["sudo"]),
                         ("say-hi", "Say hello", "echo hi", True))

    def test_edit_keeps_fields_it_does_not_know_about(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            json.dump([{"id": "pkg", "name": "Pkg", "command": "echo {x}",
                        "params": [{"name": "x", "prompt": "x"}], "note": "keep me"}], f)
        edit_custom("pkg", {"description": "now with text"}, path=self.path)
        raw = self.stored()[0]
        self.assertEqual((raw["note"], raw["params"][0]["name"], raw["description"]),
                         ("keep me", "x", "now with text"))

    def test_new_command_replaces_per_package_manager_variants(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            json.dump([{"id": "multi", "name": "Multi", "commands": {"apt": "a", "pacman": "p"}}], f)
        edit_custom("multi", {"command": "echo universal"}, path=self.path)
        raw = self.stored()[0]
        self.assertEqual(raw["command"], "echo universal")
        self.assertNotIn("commands", raw)

    def test_edit_errors(self):
        add_custom("Say hi", "echo hi", path=self.path)
        self.assertEqual(self.error_code(edit_custom, "disk-usage", {"name": "x"}, path=self.path), "builtin_readonly")
        self.assertEqual(self.error_code(edit_custom, "nope", {"name": "x"}, path=self.path), "unknown_id")
        self.assertEqual(self.error_code(edit_custom, "say-hi", {"command": ""}, path=self.path), "invalid_entry")
        self.assertEqual(self.stored()[0]["command"], "echo hi")  # the bad edit changed nothing

    # ---- delete
    def test_delete_removes_only_that_entry(self):
        add_custom("One", "true", path=self.path)
        add_custom("Two", "true", path=self.path)
        removed = delete_custom("one", path=self.path)
        self.assertEqual(removed["name"], "One")
        self.assertEqual([e["id"] for e in self.stored()], ["two"])

    def test_delete_errors(self):
        add_custom("One", "true", path=self.path)
        self.assertEqual(self.error_code(delete_custom, "disk-usage", path=self.path), "builtin_readonly")
        self.assertEqual(self.error_code(delete_custom, "nope", path=self.path), "unknown_id")
        self.assertEqual(len(self.stored()), 1)

    # ---- damaged files are never overwritten
    def test_unreadable_file_blocks_writes_and_is_left_untouched(self):
        os.makedirs(os.path.dirname(self.path))
        for content in ("{broken", '{"not": "a list"}'):
            with open(self.path, "w") as f:
                f.write(content)
            self.assertEqual(self.error_code(add_custom, "X", "true", path=self.path), "catalog_unreadable")
            self.assertEqual(self.error_code(delete_custom, "x", path=self.path), "catalog_unreadable")
            with open(self.path) as f:
                self.assertEqual(f.read(), content)

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(read_custom_raw(self.path), [])

    # ---- describe
    def test_describe_resolves_for_the_machine(self):
        e = describe_custom({"name": "Hi", "command": "echo hi"}, OTHER)
        self.assertEqual((e["id"], e["command"], e["source"]), ("hi", "echo hi", "custom"))
        multi = {"name": "Multi", "commands": {"apt": "a"}}
        self.assertEqual(describe_custom(multi, DEBIAN)["command"], "a")
        self.assertIsNone(describe_custom(multi, OTHER)["command"])


if __name__ == "__main__":
    unittest.main()

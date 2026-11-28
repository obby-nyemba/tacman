import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from tacman import cli
from tacman.detect import Machine

ROOT = Machine(distro_id="debian", family="debian", pkg_manager="apt", is_root=True)
USER = Machine(distro_id="debian", family="debian", pkg_manager="apt", is_root=False)


class CliTestCase(unittest.TestCase):
    machine = ROOT

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.catalog = os.path.join(tmp.name, "catalog.json")
        for patcher in (mock.patch.dict(os.environ, {"TACMAN_CATALOG": self.catalog}),
                        mock.patch("tacman.cli.detect_machine", return_value=self.machine)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                code = cli.main(list(argv))
            except SystemExit as exc:  # argparse usage errors: JSON error printed, exit code 2
                code = exc.code
        return code, [json.loads(line) for line in out.getvalue().splitlines()]

    def ok(self, *argv):
        code, (doc,) = self.run_cli(*argv)
        self.assertEqual((code, doc["ok"]), (0, True), doc)
        return doc["data"]

    def err(self, argv, exit_code, error_code):
        code, (doc,) = self.run_cli(*argv)
        self.assertEqual((code, doc["ok"], doc["error"]["code"]), (exit_code, False, error_code), doc)
        return doc["error"]


class PlanTests(CliTestCase):
    def test_jobs_come_back_in_order_with_quoted_params(self):
        data = self.ok("plan", "disk-usage", "install-package", "--param", "package=nginx; rm -rf ~")
        self.assertEqual([j["id"] for j in data["jobs"]], ["disk-usage", "install-package"])
        self.assertEqual(data["jobs"][1]["command"], "apt install -y 'nginx; rm -rf ~'")
        self.assertEqual(data["jobs"][0]["argv"], ["sh", "-c", "df -h"])
        self.assertEqual((data["needs_confirmation"], data["missing_params"]), ([], []))

    def test_risky_ids_and_missing_params_are_reported_not_errors(self):
        data = self.ok("plan", "restart-networking", "install-package", "restart-networking")
        self.assertEqual(data["needs_confirmation"], ["restart-networking"])  # once, even if listed twice
        self.assertEqual(data["missing_params"], [{"id": "install-package", "name": "package"}])
        self.assertIn("{package}", data["jobs"][1]["command"])
        self.assertEqual(len(data["jobs"]), 3)

    def test_unknown_id(self):
        self.err(["plan", "nope"], 3, "unknown_id")

    def test_plan_changes_nothing(self):
        self.ok("plan", "restart-networking")
        self.assertFalse(os.path.exists(self.catalog))

    def test_namespaced_param_is_used(self):
        data = self.ok("plan", "install-package", "remove-package",
                       "--param", "install-package.package=a", "--param", "remove-package.package=b")
        self.assertEqual([j["command"] for j in data["jobs"]],
                         ["apt install -y a", "apt remove -y b"])


class PlanAsNormalUserTests(CliTestCase):
    machine = USER

    def test_sudo_entries_get_the_sudo_prefix(self):
        data = self.ok("plan", "update-system")
        self.assertEqual(data["jobs"][0]["argv"][:4], ["sudo", "-n", "sh", "-c"])
        self.assertTrue(data["jobs"][0]["sudo"])


class CustomCommandTests(CliTestCase):
    def test_add_returns_the_entry_and_it_shows_up_in_list(self):
        entry = self.ok("custom", "add", "--name", "Docker containers", "--command", "docker ps",
                        "--description", "running containers", "--category", "Docker", "--sudo")
        self.assertEqual((entry["id"], entry["category"], entry["sudo"], entry["source"]),
                         ("docker-containers", "Docker", True, "custom"))
        listed = {e["id"]: e for e in self.ok("list")["entries"]}
        self.assertEqual(listed["docker-containers"]["command"], "docker ps")
        self.assertEqual(self.ok("list", "--category", "Docker")["entries"][0]["id"], "docker-containers")

    def test_a_custom_entry_can_be_planned_and_run(self):
        self.ok("custom", "add", "--name", "Say hi", "--command", "echo hi from custom")
        self.assertEqual(self.ok("plan", "say-hi")["jobs"][0]["command"], "echo hi from custom")
        code, lines = self.run_cli("run", "say-hi")
        self.assertEqual(code, 0)
        self.assertIn("hi from custom", [l["text"] for l in lines if l["event"] == "output"])

    def test_add_errors(self):
        self.err(["custom", "add", "--name", "!!!", "--command", "true"], 2, "invalid_entry")
        self.err(["custom", "add", "--name", "X", "--command="], 2, "invalid_entry")
        self.err(["custom", "add", "--name", "X", "--command", "true", "--id", "disk-usage"], 2, "invalid_entry")
        self.err(["custom", "add", "--name", "X"], 2, "usage")  # --command is required
        self.assertFalse(os.path.exists(self.catalog))

    def test_command_text_may_start_with_a_dash(self):
        entry = self.ok("custom", "add", "--name", "Dash", "--command=-weird command")
        self.assertEqual(entry["command"], "-weird command")

    def test_edit(self):
        self.ok("custom", "add", "--name", "Say hi", "--command", "echo hi")
        edited = self.ok("custom", "edit", "say-hi", "--name", "Say hello", "--risky")
        self.assertEqual((edited["id"], edited["name"], edited["command"], edited["risky"]),
                         ("say-hi", "Say hello", "echo hi", True))
        edited = self.ok("custom", "edit", "say-hi", "--no-risky", "--sudo", "--command", "echo yo")
        self.assertEqual((edited["risky"], edited["sudo"], edited["command"]), (False, True, "echo yo"))

    def test_edit_errors(self):
        self.ok("custom", "add", "--name", "Say hi", "--command", "echo hi")
        self.err(["custom", "edit", "nope", "--name", "x"], 3, "unknown_id")
        self.err(["custom", "edit", "disk-usage", "--name", "x"], 2, "builtin_readonly")
        self.err(["custom", "edit", "say-hi"], 2, "usage")  # nothing to change
        self.err(["custom", "edit", "say-hi", "--sudo", "--no-sudo"], 2, "usage")

    def test_delete(self):
        self.ok("custom", "add", "--name", "Say hi", "--command", "echo hi")
        data = self.ok("custom", "delete", "say-hi")
        self.assertEqual((data["id"], data["deleted"]), ("say-hi", True))
        self.assertNotIn("say-hi", [e["id"] for e in self.ok("list")["entries"]])
        self.err(["custom", "delete", "say-hi"], 3, "unknown_id")
        self.err(["custom", "delete", "disk-usage"], 2, "builtin_readonly")

    def test_damaged_catalog_is_reported_and_left_alone(self):
        with open(self.catalog, "w") as f:
            f.write("{broken")
        self.err(["custom", "add", "--name", "X", "--command", "true"], 1, "catalog_unreadable")
        with open(self.catalog) as f:
            self.assertEqual(f.read(), "{broken")

    def test_custom_without_a_subcommand_is_a_usage_error(self):
        self.err(["custom"], 2, "usage")


if __name__ == "__main__":
    unittest.main()

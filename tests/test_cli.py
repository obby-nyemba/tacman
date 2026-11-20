import contextlib
import io
import json
import os
import unittest
from unittest import mock

from tacman import cli
from tacman.detect import Machine

DEBIAN = Machine(distro_id="debian", family="debian", pkg_manager="apt", is_root=True)


def call(*argv):
    out, err = io.StringIO(), io.StringIO()
    env = {"TACMAN_CATALOG": "/nonexistent/catalog.json"}
    with mock.patch.dict(os.environ, env), \
         mock.patch("tacman.cli.detect_machine", return_value=DEBIAN), \
         contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    return code, lines


class CliTests(unittest.TestCase):
    def test_list_returns_entries(self):
        code, (doc,) = call("list", "--json")
        self.assertEqual((code, doc["ok"], doc["schema"]), (0, True, 1))
        got = {e["id"] for e in doc["data"]["entries"]}
        self.assertIn("update-system", got)

    def test_list_filters(self):
        _, (doc,) = call("list", "--category", "disk")
        self.assertTrue(all(e["category"] == "Disk" for e in doc["data"]["entries"]))
        _, (doc,) = call("list", "--search", "orphan")
        self.assertEqual([e["id"] for e in doc["data"]["entries"]], ["list-orphan-packages"])

    def test_info(self):
        with mock.patch("tacman.cli.detect_machine", return_value=DEBIAN):
            code, (doc,) = call("info", "--json")
        self.assertEqual(doc["data"]["family"], "debian")
        self.assertTrue(doc["data"]["supported"])

    def test_unknown_id_exits_3(self):
        code, (doc,) = call("run", "nope", "--json")
        self.assertEqual((code, doc["error"]["code"]), (3, "unknown_id"))

    def test_risky_needs_confirmation_and_nothing_runs(self):
        code, lines = call("run", "disk-usage", "restart-networking", "--json")
        self.assertEqual(code, 4)
        self.assertEqual(len(lines), 1)  # one error object, no queue events
        self.assertEqual(lines[0]["error"]["details"]["ids"], ["restart-networking"])

    def test_dry_run_skips_confirmation_and_quotes_params(self):
        code, lines = call("run", "install-package", "--param", "package=nginx; rm -rf ~", "--dry-run")
        self.assertEqual(code, 0)
        start = [l for l in lines if l["event"] == "job_start"][0]
        self.assertEqual(start["command"], "apt install -y 'nginx; rm -rf ~'")

    def test_missing_param_exits_2(self):
        code, (doc,) = call("run", "install-package")
        self.assertEqual((code, doc["error"]["code"]), (2, "missing_param"))

    def test_bad_param_format_exits_2(self):
        code, (doc,) = call("run", "install-package", "--param", "oops")
        self.assertEqual((code, doc["error"]["code"]), (2, "usage"))

    def test_real_run_streams_events(self):
        code, lines = call("run", "kernel-hostname", "--json")
        self.assertEqual(code, 0)
        self.assertEqual([l["event"] for l in lines if l["event"] != "output"],
                         ["queue_start", "job_start", "job_end", "queue_end"])
        self.assertTrue(any(l["event"] == "output" for l in lines))

    def test_usage_error_is_json(self):
        with self.assertRaises(SystemExit) as ctx:
            call("run")
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()

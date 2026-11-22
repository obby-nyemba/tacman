import contextlib
import io
import os
import unittest
from unittest import mock

from tacman import menu


def entry(id_, name="", description="", category="Misc", sudo=False, risky=False, params=None, command="true"):
    return {"id": id_, "name": name or id_, "description": description, "category": category,
            "command": command, "sudo": sudo, "risky": risky, "params": params or [], "source": "builtin"}


ENTRIES = [
    entry("kernel", "Kernel & hostname", "uname -a"),
    entry("memory", "Memory usage", "RAM usage summary"),
    entry("disk-usage", "Disk usage", "filesystem space"),
    entry("disk-deep", "Disk deep scan", "check disks thoroughly"),
    entry("uptime", "Uptime", "uptime and load"),
]


class SelectionTests(unittest.TestCase):
    def check(self, text, want):
        self.assertEqual(menu.parse_selection(text, ENTRIES), want, text)

    def test_numbers_ranges_and_keywords(self):
        self.check("1 3-5", [1, 3, 4, 5])
        self.check("disk", [3, 4])
        self.check("1 3-5 disk", [1, 3, 4, 5])   # disk adds nothing new
        self.check("5-3", [5, 4, 3])             # reversed range
        self.check("2-2", [2])
        self.check("0 1 99", [1])                # out of range ignored
        self.check("", [])
        self.check("zzz", [])
        self.check("DISK-usage", [3])            # keyword with a dash, any case

    def test_huge_ranges_are_clamped_not_looped(self):
        self.check("1-9999999999", [1, 2, 3, 4, 5])
        self.check("4-9999999999", [4, 5])
        self.check("0-2", [1, 2])
        self.check("98-99", [])                  # entirely outside the menu
        self.check("0-0", [])


class QueueTests(unittest.TestCase):
    def review(self, queue, *answers):
        with mock.patch("builtins.input", side_effect=list(answers) + [EOFError()]), \
             contextlib.redirect_stdout(io.StringIO()):
            return menu.review_queue(queue)

    def test_move_delete_and_run(self):
        a, b, c = ENTRIES[:3]
        got = self.review([a, b, c], "move 3 1", "del 2", "run")
        self.assertEqual([e["id"] for e in got], ["disk-usage", "memory"])

    def test_quit_goes_back(self):
        self.assertIsNone(self.review(ENTRIES[:2], "quit"))

    def test_bad_input_is_ignored(self):
        got = self.review(ENTRIES[:2], "", "del 9", "del x", "move 1", "move 0 1", "dance", "run")
        self.assertEqual(len(got), 2)

    def test_deleting_everything_returns_none(self):
        self.assertIsNone(self.review(ENTRIES[:1], "del 1"))


class ParamAndRiskTests(unittest.TestCase):
    PKG = [{"name": "package", "prompt": "package name", "required": True}]

    def test_params_are_namespaced_asked_once_and_required(self):
        inst = entry("install-package", "Install package", params=self.PKG)
        rem = entry("remove-package", "Remove package", params=self.PKG)
        with mock.patch("builtins.input", side_effect=["", "nginx", "htop"]), \
             contextlib.redirect_stdout(io.StringIO()):
            got = menu.collect_params([inst, rem, inst])
        self.assertEqual(got, {"install-package.package": "nginx", "remove-package.package": "htop"})

    def test_optional_param_may_be_blank(self):
        e = entry("x", params=[{"name": "n", "prompt": "n", "required": False}])
        with mock.patch("builtins.input", side_effect=[""]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(menu.collect_params([e]), {})

    def test_risky_needs_a_literal_yes(self):
        safe, danger = entry("safe"), entry("danger", risky=True)
        with mock.patch("builtins.input", side_effect=["y"]), contextlib.redirect_stdout(io.StringIO()):
            kept, approved = menu.confirm_risky([safe, danger])
        self.assertEqual(([e["id"] for e in kept], approved), (["safe"], set()))
        with mock.patch("builtins.input", side_effect=["yes"]), contextlib.redirect_stdout(io.StringIO()):
            kept, approved = menu.confirm_risky([safe, danger])
        self.assertEqual(([e["id"] for e in kept], approved), (["safe", "danger"], {"danger"}))

    def test_print_menu_groups_by_category_and_shows_flags(self):
        items = [entry("a", "Alpha", category="Disk", sudo=True), entry("b", "Beta", category="Disk", risky=True),
                 entry("c", "Gamma", category="Network")]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            menu.print_menu(items)
        text = out.getvalue()
        self.assertEqual(text.count("== Disk =="), 1)
        self.assertLess(text.index("== Disk =="), text.index("== Network =="))
        self.assertIn("[sudo]", text)
        self.assertIn("[RISKY]", text)


class EngineIntegrationTests(unittest.TestCase):
    """These start the real engine (python -m tacman) from the project root."""

    ENV = {"TACMAN_CATALOG": "/nonexistent/catalog.json"}

    def session(self, *answers):
        out = io.StringIO()
        with mock.patch.dict(os.environ, self.ENV), \
             mock.patch("builtins.input", side_effect=list(answers) + [EOFError()]), \
             contextlib.redirect_stdout(out):
            code = menu.main()
        return code, out.getvalue()

    def test_run_job_streams_output(self):
        e = entry("kernel-hostname", "Kernel & hostname")
        out = io.StringIO()
        with mock.patch.dict(os.environ, self.ENV), contextlib.redirect_stdout(out):
            result = menu.run_job(e, {}, False)
        self.assertEqual(result["status"], "ok")
        self.assertIn("  | ", out.getvalue())

    def test_full_session_runs_a_job(self):
        code, text = self.session("kernel", "run", "q")
        self.assertEqual(code, 0)
        self.assertIn("[1/1] Kernel & hostname", text)
        self.assertIn("OK: Kernel & hostname", text)
        self.assertIn("Done: 1 ok, 0 failed.", text)

    def test_declined_risky_job_never_runs(self):
        code, text = self.session("networking", "run", "no", "q")
        self.assertEqual(code, 0)
        self.assertIn("skipped by user", text)
        self.assertIn("nothing left to run", text)
        self.assertNotIn("[1/1]", text)

    def test_nothing_matched_and_quit(self):
        code, text = self.session("zzzz", "q")
        self.assertEqual(code, 0)
        self.assertIn("nothing matched", text)

    def test_end_of_input_exits_cleanly(self):
        code, text = self.session()  # input() raises EOFError straight away
        self.assertEqual(code, 0)
        self.assertIn("bye.", text)


if __name__ == "__main__":
    unittest.main()

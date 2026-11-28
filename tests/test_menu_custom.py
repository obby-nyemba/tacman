import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from tacman import menu
from tacman.catalog import add_custom


class MenuCustomTests(unittest.TestCase):
    """Real menu sessions (and the real engine) against a temporary catalog file."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "catalog.json")
        patcher = mock.patch.dict(os.environ, {"TACMAN_CATALOG": self.path})
        patcher.start()
        self.addCleanup(patcher.stop)

    def session(self, *answers):
        out = io.StringIO()
        with mock.patch("builtins.input", side_effect=list(answers) + [EOFError()]), \
             contextlib.redirect_stdout(out):
            code = menu.main()
        return code, out.getvalue()

    def stored(self):
        with open(self.path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_add_run_and_delete_in_one_session(self):
        code, text = self.session(
            "add", "Say hi", "demo", "", "echo hi from the menu", "n", "n",   # add (category left empty)
            "say-hi", "run", "",                                              # pick it, run it, back to menu
            "delete", "1", "yes",                                             # delete it again
            "q")
        self.assertEqual(code, 0)
        self.assertIn("added 'Say hi' (id: say-hi)", text)
        self.assertIn("== Custom ==", text)
        self.assertIn("hi from the menu", text)
        self.assertIn("Done: 1 ok, 0 failed.", text)
        self.assertIn("deleted", text)
        self.assertEqual(self.stored(), [])

    def test_add_can_be_cancelled(self):
        _, text = self.session("add", "", "q")
        self.assertIn("cancelled", text)
        self.assertFalse(os.path.exists(self.path))
        _, text = self.session("add", "Name", "desc", "", "", "q")  # empty command
        self.assertIn("a command is required", text)
        self.assertFalse(os.path.exists(self.path))

    def test_flags_are_passed_on(self):
        self.session("add", "Danger", "", "Ops", "echo x", "y", "y", "q")
        entry = self.stored()[0]
        self.assertEqual((entry["category"], entry["sudo"], entry["risky"]), ("Ops", True, True))

    def test_delete_with_nothing_to_delete(self):
        _, text = self.session("delete", "q")
        self.assertIn("haven't added any commands of your own", text)

    def test_delete_asks_for_confirmation_and_checks_the_number(self):
        add_custom("Keep me", "true", path=self.path)
        _, text = self.session("delete", "9", "delete", "1", "no", "delete", "", "q")
        self.assertIn("bad number", text)
        self.assertIn("kept", text)
        self.assertEqual([e["id"] for e in self.stored()], ["keep-me"])


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import json
import os
import unittest
from unittest import mock

from tacman import cli
from tacman.detect import Machine

DEBIAN = Machine(distro_id="debian", family="debian", pkg_manager="apt", is_root=True)
ENV = {"TACMAN_CATALOG": "/nonexistent/catalog.json"}


def run_cli(*argv):
    out = io.StringIO()
    with mock.patch.dict(os.environ, ENV), mock.patch("tacman.cli.detect_machine", return_value=DEBIAN), \
         contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(list(argv))
    return code, out.getvalue()


class PrettyAndMenuTests(unittest.TestCase):
    def test_pretty_is_indented_but_same_data(self):
        _, compact = run_cli("list")
        code, pretty = run_cli("list", "--pretty")
        self.assertEqual(code, 0)
        self.assertGreater(pretty.count("\n"), 50)
        self.assertEqual(json.loads(pretty), json.loads(compact))

    def test_compact_output_is_a_single_line(self):
        _, compact = run_cli("list")
        self.assertEqual(compact.count("\n"), 1)

    def test_bare_command_in_a_pipe_is_a_usage_error(self):
        with mock.patch("tacman.cli._interactive", return_value=False):
            with self.assertRaises(SystemExit) as ctx:
                run_cli()
        self.assertEqual(ctx.exception.code, 2)

    def test_bare_command_in_a_terminal_opens_the_menu(self):
        with mock.patch("tacman.cli._interactive", return_value=True), \
             mock.patch("tacman.menu.main", return_value=0) as menu_main:
            code, _ = run_cli()
        self.assertEqual(code, 0)
        menu_main.assert_called_once()

    def test_menu_subcommand(self):
        with mock.patch("tacman.menu.main", return_value=0) as menu_main:
            code, _ = run_cli("menu")
        self.assertEqual(code, 0)
        menu_main.assert_called_once()


if __name__ == "__main__":
    unittest.main()

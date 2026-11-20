import unittest

from tacman.catalog import Entry
from tacman.detect import Machine
from tacman.runner import fill_params, make_job, resolve_params, run_queue

ROOT = Machine(is_root=True)
USER = Machine(is_root=False)


def entry(id_, command, sudo=False, params=None):
    return Entry(id=id_, name=id_, description="", category="Misc", command=command,
                 sudo=sudo, params=params or [])


class Collector:
    def __init__(self):
        self.events = []

    def __call__(self, event, **fields):
        self.events.append(dict(fields, event=event))

    def names(self):
        return [e["event"] for e in self.events]

    def of(self, name):
        return [e for e in self.events if e["event"] == name]


def run(entries, **kwargs):
    emit = Collector()
    code = run_queue([make_job(e, {}, ROOT) for e in entries], emit, **kwargs)
    return code, emit


class ParamTests(unittest.TestCase):
    PKG = [{"name": "package", "prompt": "package name", "required": True}]

    def test_values_are_shell_quoted(self):
        e = entry("inst", "apt install -y {package}", params=self.PKG)
        self.assertEqual(fill_params(e, {"package": "nginx"}), "apt install -y nginx")
        evil = fill_params(e, {"package": "nginx; rm -rf ~"})
        self.assertEqual(evil, "apt install -y 'nginx; rm -rf ~'")

    def test_undeclared_braces_are_left_alone(self):
        e = entry("x", "find . -exec ls {} + | awk '{print $1}'")
        self.assertEqual(fill_params(e, {}), e.command)

    def test_namespaced_param_beats_plain(self):
        e = entry("inst", "x {package}", params=self.PKG)
        values, missing = resolve_params(e, {"package": "a", "inst.package": "b"})
        self.assertEqual((values, missing), ({"package": "b"}, []))

    def test_missing_required_param(self):
        e = entry("inst", "x {package}", params=self.PKG)
        self.assertEqual(resolve_params(e, {})[1], ["package"])
        self.assertEqual(resolve_params(e, {"package": ""})[1], ["package"])


class JobTests(unittest.TestCase):
    def test_sudo_prefix_only_when_not_root(self):
        e = entry("s", "true", sudo=True)
        self.assertEqual(make_job(e, {}, USER).argv, ["sudo", "-n", "sh", "-c", "true"])
        self.assertEqual(make_job(e, {}, ROOT).argv, ["sh", "-c", "true"])


class QueueTests(unittest.TestCase):
    def test_success_streams_output_in_order(self):
        code, emit = run([entry("a", "echo hi; echo oops >&2"), entry("b", "echo two")])
        self.assertEqual(code, 0)
        self.assertEqual(emit.names()[0], "queue_start")
        self.assertEqual(emit.names()[-1], "queue_end")
        outputs = {(e["id"], e["stream"], e["text"]) for e in emit.of("output")}
        self.assertEqual(outputs, {("a", "stdout", "hi"), ("a", "stderr", "oops"), ("b", "stdout", "two")})
        self.assertEqual([e["status"] for e in emit.of("job_end")], ["ok", "ok"])
        self.assertEqual(emit.of("queue_end")[0]["completed"], 2)

    def test_jobs_run_one_at_a_time_in_order(self):
        _, emit = run([entry("a", "echo 1"), entry("b", "echo 2")])
        order = [(e["event"], e["id"]) for e in emit.events if e["event"] in ("job_start", "job_end")]
        self.assertEqual(order, [("job_start", "a"), ("job_end", "a"), ("job_start", "b"), ("job_end", "b")])

    def test_failure_aborts_by_default(self):
        code, emit = run([entry("bad", "exit 3"), entry("never", "echo no")])
        self.assertEqual(code, 5)
        ends = emit.of("job_end")
        self.assertEqual((ends[0]["status"], ends[0]["exit_code"]), ("failed", 3))
        self.assertEqual((ends[1]["status"], ends[1]["reason"]), ("skipped", "aborted"))
        self.assertEqual(emit.of("queue_end")[0]["ok"], False)

    def test_continue_after_failure(self):
        code, emit = run([entry("bad", "exit 1"), entry("good", "echo yes")], on_failure="continue")
        self.assertEqual(code, 5)
        self.assertEqual([e["status"] for e in emit.of("job_end")], ["failed", "ok"])

    def test_dry_run_executes_nothing(self):
        code, emit = run([entry("x", "exit 9")], dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(emit.of("job_end")[0]["reason"], "dry_run")
        self.assertEqual(emit.of("output"), [])
        self.assertEqual(emit.of("job_start")[0]["argv"], ["sh", "-c", "exit 9"])

    def test_missing_program_is_reported_not_raised(self):
        job = make_job(entry("x", "true"), {}, ROOT)
        job.argv = ["/definitely/not/a/program"]
        emit = Collector()
        code = run_queue([job], emit)
        self.assertEqual(code, 5)
        self.assertEqual(emit.of("job_end")[0]["reason"], "spawn_failed")

    def test_sudo_password_required_is_recognised(self):
        e = entry("s", "echo 'sudo: a password is required' >&2; exit 1", sudo=True)
        emit = Collector()
        run_queue([make_job(e, {}, ROOT)], emit)  # root => runs directly, fakes sudo's message
        self.assertEqual(emit.of("job_end")[0]["reason"], "sudo_password_required")


if __name__ == "__main__":
    unittest.main()

"""Building and running jobs: parameter filling, sudo handling, and the
sequential queue that streams JSON Lines events (see INTERFACE.md)."""
from __future__ import annotations

import json
import queue
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Tuple

from . import SCHEMA
from .catalog import Entry
from .detect import Machine

PLACEHOLDER = re.compile(r"\{(\w+)\}")
SUDO_HINTS = ("a password is required", "a terminal is required")

Emit = Callable[..., None]


@dataclass
class Job:
    entry: Entry
    command: str      # after parameter substitution
    argv: List[str]   # what actually gets executed


def resolve_params(entry: Entry, supplied: Dict[str, str]) -> Tuple[Dict[str, str], List[str]]:
    """Pick values for the entry's params. `id.name` beats plain `name`.
    Returns (values, names of missing required params)."""
    values: Dict[str, str] = {}
    missing: List[str] = []
    for param in entry.params:
        name = param["name"]
        value = supplied.get("%s.%s" % (entry.id, name), supplied.get(name))
        if value is None:
            value = ""
        if param.get("required", True) and value == "":
            missing.append(name)
            continue
        values[name] = value
    return values, missing


def fill_params(entry: Entry, values: Dict[str, str]) -> str:
    """Substitute {name} placeholders with shell-quoted values.

    Only declared params are replaced, so shell/awk braces like {print $3}
    or find's {} are left alone.
    """
    declared = {p["name"] for p in entry.params}

    def substitute(match):
        name = match.group(1)
        if name in declared and name in values:
            return shlex.quote(values[name])
        return match.group(0)

    return PLACEHOLDER.sub(substitute, entry.command)


def make_job(entry: Entry, values: Dict[str, str], machine: Machine) -> Job:
    command = fill_params(entry, values)
    prefix = ["sudo", "-n"] if entry.sudo and not machine.is_root else []
    return Job(entry=entry, command=command, argv=prefix + ["sh", "-c", command])


def make_emitter(stream) -> Emit:
    """emit("job_start", id="x") writes one JSON line and flushes it."""
    def emit(event: str, **fields) -> None:
        record = {
            "schema": SCHEMA,
            "event": event,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        }
        record.update(fields)
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
    return emit


def _execute(job: Job, emit: Emit) -> Dict[str, object]:
    """Run one job, streaming its output. Returns status/exit_code/reason."""
    try:
        proc = subprocess.Popen(
            job.argv, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
    except OSError as exc:
        return {"status": "failed", "exit_code": None,
                "reason": "spawn_failed", "message": str(exc)}

    lines: "queue.Queue" = queue.Queue()

    def pump(stream, name):
        try:
            for line in stream:
                lines.put((name, line.rstrip("\r\n")))
        finally:
            lines.put((name, None))

    for stream, name in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
        threading.Thread(target=pump, args=(stream, name), daemon=True).start()

    stderr_tail: List[str] = []
    open_streams = 2
    try:
        while open_streams:
            name, text = lines.get()
            if text is None:
                open_streams -= 1
                continue
            emit("output", id=job.entry.id, stream=name, text=text)
            if name == "stderr":
                stderr_tail = (stderr_tail + [text])[-20:]
        exit_code = proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        return {"status": "failed", "exit_code": None, "reason": "interrupted"}
    finally:
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass

    if exit_code == 0:
        return {"status": "ok", "exit_code": 0}
    result: Dict[str, object] = {"status": "failed", "exit_code": exit_code}
    if job.entry.sudo and any(h in line for line in stderr_tail for h in SUDO_HINTS):
        result["reason"] = "sudo_password_required"
    return result


def run_queue(jobs: List[Job], emit: Emit, on_failure: str = "abort", dry_run: bool = False) -> int:
    """Run jobs strictly one at a time. Returns the process exit code:
    0 all ok, 5 a job failed, 130 interrupted."""
    total = len(jobs)
    emit("queue_start", total=total, ids=[j.entry.id for j in jobs], dry_run=dry_run)
    completed = failed = skipped = 0
    stop_reason = None

    for index, job in enumerate(jobs, 1):
        entry = job.entry
        if stop_reason:
            emit("job_end", index=index, id=entry.id, status="skipped", reason=stop_reason)
            skipped += 1
            continue

        emit("job_start", index=index, total=total, id=entry.id, name=entry.name,
             command=job.command, argv=job.argv)
        if dry_run:
            emit("job_end", index=index, id=entry.id, status="skipped",
                 reason="dry_run", exit_code=None, duration_ms=0)
            skipped += 1
            continue

        started = time.monotonic()
        result = _execute(job, emit)
        fields = {
            "index": index, "id": entry.id, "status": result["status"],
            "exit_code": result["exit_code"],
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        for key in ("reason", "message"):
            if result.get(key):
                fields[key] = result[key]
        emit("job_end", **fields)

        if result["status"] == "ok":
            completed += 1
        else:
            failed += 1
            if result.get("reason") == "interrupted":
                stop_reason = "interrupted"
            elif on_failure == "abort":
                stop_reason = "aborted"

    emit("queue_end", ok=failed == 0, completed=completed, failed=failed, skipped=skipped)
    if stop_reason == "interrupted":
        return 130
    return 0 if failed == 0 else 5

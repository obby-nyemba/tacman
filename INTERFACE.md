# tacman JSON Interface (schema 1) - DRAFT

**Implementation status (engine 0.1.0):** `info`, `list` and `run` (including
`--dry-run`) are implemented and tested. `plan` and the `custom` commands are
specified below but not built yet; `run --dry-run` covers what `plan` is for.

tacman is a Python engine. Front-ends and tools written in any language use it by
running the `tacman` command with `--json` and reading what it prints. This file
is the team agreement: if the engine and this document disagree, fix one of them
right away.

## 1. Ground rules

- **stdout carries JSON only.** Human-readable messages and debug logs go to stderr.
- **UTF-8** everywhere.
- **Every response has `"schema": 1`.** Breaking changes bump this number. Adding
  new fields is not breaking, so clients must **ignore fields they don't know**.
- **Refer to entries by `id`, never by position.** Menu order can change; ids don't.
- The engine never prompts. It is non-interactive: stdin is closed for jobs, and
  anything a command needs (like a package name) is passed in as a parameter.

## 1a. Supported platforms (v1)

The first release targets two families only:

| Family | Package manager | Covers (via `ID` / `ID_LIKE`) |
|--------|-----------------|-------------------------------|
| Debian | `apt` | Debian, Ubuntu, Linux Mint, Pop!_OS, Raspberry Pi OS, Kali |
| Arch | `pacman` | Arch, Manjaro, EndeavourOS |

- Detection checks `ID` first, then each word in `ID_LIKE`, and finally which
  package manager binary exists.
- On any other distro the engine still starts: `info` reports
  `"supported": false`, and `list` returns only universal entries (those with a
  single `command`, such as `df -h`). Package-specific entries stay hidden.
- Dnf, zypper and apk are planned for a later release. The catalog format already
  allows their keys, so adding them later changes no front-end.

## 2. Response shapes

Most commands print a single JSON object and exit.

Success:

```json
{ "schema": 1, "ok": true, "data": { } }
```

Failure:

```json
{ "schema": 1, "ok": false, "error": { "code": "unknown_id", "message": "No entry with id 'disk-usagee'" } }
```

`code` is for programs, `message` is for humans.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Unexpected engine error |
| 2 | Bad usage / invalid arguments |
| 3 | Unknown entry id |
| 4 | Confirmation required (risky entries not approved) |
| 5 | A job in the queue failed (for `run`) |
| 130 | `run` was interrupted (Ctrl-C) |

## 3. The entry object

```json
{
  "id": "update-system",
  "name": "Update system",
  "description": "refresh + upgrade all packages",
  "category": "Packages",
  "command": "apt update && apt upgrade -y",
  "sudo": true,
  "risky": false,
  "params": [],
  "source": "builtin"
}
```

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | Stable, lowercase, `a-z0-9-`. Unique. |
| `name` | string | Display name. |
| `description` | string | One line. |
| `category` | string | Free text; builtin ones: System info, Packages, Disk, Network, Users, Services, Misc. |
| `command` | string | Already resolved for this machine. Always run via `sh -c`, so catalog commands must be plain POSIX shell. |
| `sudo` | bool | Engine runs it with `sudo -n`. |
| `risky` | bool | Needs explicit confirmation before it runs. |
| `params` | array | Inputs the entry needs, see below. Empty if none. |
| `source` | string | `"builtin"` or `"custom"`. Only custom entries can be edited or deleted. |

### Params

Entries that used to prompt (install package, DNS lookup...) declare what they need:

```json
"params": [ { "name": "package", "prompt": "package name", "required": true } ]
```

In the catalog, the command uses a placeholder such as `apt install -y {package}`.
The engine shell-quotes the value before substituting it, so a value like
`nginx; rm -rf ~` cannot inject extra commands. Front-ends ask the user and pass
values with `--param package=nginx`. If one queue has two entries that need the
same param name (install and remove both want `package`), use the namespaced form
`--param install-package.package=nginx`, which wins over the plain form.

## 4. Commands

### `tacman info --json`

```json
{ "schema": 1, "ok": true, "data": {
    "engine_version": "0.1.0",
    "distro_id": "ubuntu",
    "distro_name": "Ubuntu",
    "id_like": ["debian"],
    "pkg_manager": "apt",
    "family": "debian",
    "supported": true,
    "is_root": false,
    "sudo_cached": false
} }
```

`family` is `"debian"`, `"arch"`, or `null` on an unsupported distro. `pkg_manager`
is `null` when none is found. `sudo_cached` is true when privileged jobs can run right now without a
password (already root, or sudo credentials are cached). If it is false and the
queue has `sudo` entries, the front-end should ask the user to run `sudo -v`.
When the engine itself runs as root it skips the `sudo` prefix entirely.

### `tacman list --json [--category NAME] [--search WORD]`

Returns only entries that work on this machine.

```json
{ "schema": 1, "ok": true, "data": { "entries": [ { "...": "entry object" } ] } }
```

### `tacman plan --json <id> [<id> ...] [--param name=value]`

Dry run. Shows exactly what would execute, in order, and changes nothing.

```json
{ "schema": 1, "ok": true, "data": {
    "jobs": [
      { "id": "disk-usage", "argv": ["sh", "-c", "df -h"], "sudo": false, "risky": false },
      { "id": "restart-networking", "argv": ["sudo", "-n", "sh", "-c", "systemctl restart NetworkManager"], "sudo": true, "risky": true }
    ],
    "needs_confirmation": ["restart-networking"],
    "missing_params": []
} }
```

### `tacman run --json <id> [<id> ...] [options]`

Runs the ids **in the order given**, one at a time. Unlike other commands this one
**streams JSON Lines**: one JSON object per line, flushed immediately.

Options:

- `--param name=value` (repeatable) fills entry params.
- `--confirm-risky id1,id2` approves specific risky entries (or `all`).
- `--on-failure continue|abort` (default `abort`).
- `--dry-run` behaves like `plan` but in event form.

Before anything runs, the engine validates the whole queue. If a risky id isn't
approved it exits with code 4 and runs nothing. If an id is unknown, code 3. If a
required param is missing, code 2.

#### Events

Every line has `"schema": 1`, an `"event"` name, and a `"ts"` (ISO 8601 UTC).

```json
{"schema":1,"event":"queue_start","ts":"...","total":2,"ids":["disk-usage","uptime-load"],"dry_run":false}
{"schema":1,"event":"job_start","ts":"...","index":1,"total":2,"id":"disk-usage","name":"Disk usage","command":"df -h","argv":["sh","-c","df -h"]}
{"schema":1,"event":"output","ts":"...","id":"disk-usage","stream":"stdout","text":"Filesystem  Size  Used ..."}
{"schema":1,"event":"job_end","ts":"...","id":"disk-usage","status":"ok","exit_code":0,"duration_ms":41}
{"schema":1,"event":"queue_end","ts":"...","ok":true,"completed":2,"failed":0,"skipped":0}
```

- `output.stream` is `"stdout"` or `"stderr"`. Each event is one line of output
  without its trailing newline.
- `job_end.status` is one of `ok`, `failed`, `skipped`.
- `job_start` includes the final `command` (params filled in) and the exact `argv`.
- A failed job includes `exit_code` and may include `reason`, for example
  `"sudo_password_required"` (front-end should have asked for `sudo -v` first).
- With `--on-failure abort`, remaining jobs get a `job_end` with `status: "skipped"`
  and `"reason": "aborted"` (no `job_start` for them), then `queue_end` follows.
  If the user interrupts, the running job ends with `reason: "interrupted"` and the
  rest are skipped with the same reason.
- With `--dry-run`, every job emits `job_start` then `job_end` with
  `status: "skipped"`, `reason: "dry_run"`; nothing executes and no confirmation is needed.
- The process exit code is 0 if every job is ok, otherwise 5.

### Custom entries

All print a normal single-object response.

```
tacman custom add --json --name "Docker containers" --description "list running containers" \
    --category Docker --command "docker ps" [--sudo] [--risky]
tacman custom edit --json <id> [--name ...] [--command ...] ...
tacman custom delete --json <id>
```

They are stored in `~/.config/tacman/catalog.json`. `add` returns the new entry with
its generated `id`. Editing or deleting a builtin entry fails with code
`builtin_readonly`.

## 5. Error codes (`error.code`)

`unknown_id`, `missing_param`, `confirmation_required`, `builtin_readonly`,
`invalid_entry`, `catalog_unreadable`, `usage`, `internal`.

## 6. Catalog file format (for people adding entries)

Catalog files are JSON arrays. An entry gives either one universal `command`, or a
`commands` object keyed by package manager. v1 supports `apt` and `pacman`;
`dnf`, `zypper` and `apk` are planned. Entries with no variant for the current
machine are hidden.

```json
[
  { "id": "disk-usage", "name": "Disk usage", "description": "filesystem space",
    "category": "Disk", "command": "df -h" },
  { "id": "update-system", "name": "Update system", "description": "refresh + upgrade all packages",
    "category": "Packages", "sudo": true,
    "commands": { "apt": "apt update && apt upgrade -y", "pacman": "pacman -Syu --noconfirm" } }
]
```

Builtin package-specific entries must provide both `apt` and `pacman` variants (a
test enforces this). A custom entry may provide just one, for example a command
that only exists on Arch; it is simply hidden on the other family.

## 7. Who owns what

| Area | Owner |
|------|-------|
| Engine (detection, catalog, queue, safety, this contract) | Obby |
| Front-end(s) | _TBD_ |
| Catalog entries / categories | _TBD_ |
| Tests and docs | _TBD_ |

## 8. Open decisions (settle these as a group)

1. Do we add a `tacman serve` mode (long-running, JSON over stdin/stdout) so a UI
   doesn't pay startup cost per call?
2. ~~`sh` or `bash`?~~ **Decided: `sh -c`.** Builtin entries are POSIX shell only; avoid
   bash-isms such as `read -p` in catalog commands (dash on Debian lacks them).
3. Do we want a run log file (`~/.local/state/tacman/runs.jsonl`)?
4. Minimum supported Python version (suggest 3.8).

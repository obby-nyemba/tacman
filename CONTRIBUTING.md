# Contributing to Tacman

Thanks for helping. Tacman is a group project: the Python engine in `tacman/`
is the core, and everything else (front-ends, catalog entries, tests, docs)
talks to it through the contract in [INTERFACE.md](INTERFACE.md).

## Getting set up

```
git clone git@github.com:obby-nyemba/tacman.git
cd tacman
python3 -m unittest discover      # should print OK
python3 -m tacman list
```

Python 3.8+ and the standard library only. Please don't add dependencies
without asking first, because Tacman has to run on a bare Debian or Arch box.

## Workflow

`main` is protected, so nobody pushes to it directly.

1. Update `main`: `git checkout main && git pull`
2. Make a branch: `git checkout -b short-description` (for example `add-docker-entries`)
3. Make your change, then run `python3 -m unittest discover`
4. Commit with a clear message: `git commit -m "Add Docker catalog entries"`
5. Push the branch: `git push -u origin short-description`
6. Open a pull request on GitHub. Say what changed and why. GitHub then runs the tests on Debian and Arch; wait for them to turn green.
7. Wait for review. Fix anything asked, push again, and it updates the same PR.

Set your git email to the one on your GitHub account (`git config user.email`)
so your commits are credited to you.

## Rules for each kind of change

**Catalog entries** (`tacman/data/builtin.json`)
- Give every entry a unique, lowercase `id` (letters, digits, `-`).
- Package commands need both an `apt` and a `pacman` variant. A test checks this.
- Commands run under `sh -c`: plain POSIX shell only, no `read -p` or other bash-isms.
- Anything that asks for input uses `params` with `{name}` placeholders, never a prompt.
- Mark anything destructive `"risky": true` and anything needing root `"sudo": true`.

**The JSON contract** (`INTERFACE.md`)
- It's the team agreement. Changing it needs the group's agreement first.
- Adding a field is fine. Removing or renaming one is a breaking change and bumps `schema`.
- Update `INTERFACE.md` in the same pull request as the code change.

**Front-ends and tools in other languages**
- Talk to the engine only through `tacman ... --json` and read its JSON. Don't
  import the Python code or read the catalog file directly.
- Refer to entries by `id`, never by their position in a list.

**Tests**
- New behavior needs a test in `tests/`. Bug fixes should add a test that fails without the fix.
- If you can, run the tests on both a Debian-family and an Arch-family machine.

## Reporting problems

Open a GitHub issue. Include your distro (`python3 -m tacman info`), the
command you ran, and the output.

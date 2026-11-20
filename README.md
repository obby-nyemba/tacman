# tacman

Pick Linux commands from a menu instead of typing them. Python engine with a
JSON interface, built for Debian and Arch.

A curated command catalog plus a sequential run queue. The Python engine is the
core; front-ends in any language talk to it through the contract in
[INTERFACE.md](INTERFACE.md).

Targets Debian-family (apt) and Arch-family (pacman) systems. Python 3.8+,
standard library only.

```
python3 -m tacman info
python3 -m tacman list [--category Disk] [--search word]
python3 -m tacman run disk-usage uptime-load
python3 -m tacman run install-package --param package=htop --dry-run
python3 -m tacman run restart-networking --confirm-risky restart-networking
```

Run the tests: `python3 -m unittest discover`

Layout: `tacman/detect.py` (distro + package manager), `tacman/catalog.py`
(builtin + custom entries), `tacman/runner.py` (queue, params, sudo),
`tacman/cli.py` (commands + JSON output), `tacman/data/builtin.json` (the catalog).
Custom entries: `~/.config/tacman/catalog.json`, same format as `builtin.json`.

## License

MIT, see [LICENSE](LICENSE).

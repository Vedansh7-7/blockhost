# Contributing to BlockHost

Thanks for helping! BlockHost is small on purpose. It should stay easy to read and hard to lose a world with.

## Ground rules
- **Never risk the world.** Any change that touches the lock, the heartbeat or saving needs a test in `tests/test_logic.py`.
- **No secrets in the repo.** Group files (`*.bhgroup`), OAuth `client_secret*.json`, world saves (`*.bhw`) are git-ignored. Keep it that way.
- **Keep the OS split clean.** Anything Windows- or macOS-specific goes in `app/core/system.py`.
- **Original art only.** No Mojang textures, logos or sounds. UI art is generated in code.

## Setup
```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-build.txt   # macOS: .venv/bin/pip
python app/main.py
```

## Tests
```bash
python tests/test_logic.py          # fast: crypto, lock, race, fencing (fake Drive)
python tests/test_logic.py --e2e    # also boots a real server on port 25599 (needs Java 25)
```
CI runs both on Windows and macOS for every pull request.

## Pull requests
1. Fork, then branch from `main`.
2. Keep PRs focused, with one change each.
3. Describe **what** changed and **how you tested it** (the PR template asks for this).
4. Update `CHANGELOG.md` under *Unreleased*.

## Good first issues
- Import an existing world from the UI
- Show a Tailscale address next to the LAN address
- New storage backend behind the same interface as `core/drive.py`

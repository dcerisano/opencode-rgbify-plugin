# Installer (install.sh)

Root-only (`sudo bash install.sh`). Logs to `/tmp/install-<timestamp>.log`. State gates in `.opencode/state/` (phase-1..4); delete a gate to force reinstall.

## Phases
1. **Node via nvm** — installs nvm v0.40.1 if missing, `nvm install 22`, alias default 22.
2. **uv** — `curl https://astral.sh/uv/install.sh | bash`; adds `~/.local/bin` to PATH.
3. **opencode CLI** — `curl https://opencode.ai/install | bash`; symlink `~/.opencode/bin/opencode` → `/usr/local/bin/opencode`. Gated: reinstall only when missing.
4. **Context7 API key** — checks env or `~/.bashrc`; warns if absent.
5. **Verify** — node/uv/opencode versions.

## Mechanics
- `run_as_user`: `sudo -u $SUDO_USER` for user-level installs (avoids root-owned files in user dirs).
- `phase_gate`: skip if gate file exists.
- `phase_gate_versioned`: re-run a version check; if version changed → reinstall + `touch restart-required`.
- `version_changed`: warns and sets `restart-required` marker (cleared at start of main).
- `SUDO_USER`/`USER_HOME` resolution for correct user home.
- Bridge deps verified as user with `-B` (no root-owned `.pyc` in user venv).
- Installer copies plugin into global opencode config (not symlink).
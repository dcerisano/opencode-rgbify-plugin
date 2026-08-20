#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SUDO_USER="${SUDO_USER:-${USER:-root}}"
USER_HOME="$(eval echo "~$SUDO_USER" 2>/dev/null || echo "${HOME:-/root}")"
LOG_FILE="/tmp/rgbify-install-$(date +%Y%m%d-%H%M%S).log"

say() { local p="$1"; shift; echo "[$p] $*" | tee -a "$LOG_FILE"; }

if [ "$(id -u)" != "0" ]; then
    echo "error: this installer must be run as root (sudo bash install.sh)"
    exit 1
fi

run_as_user() {
    if [ "$(id -u)" = "0" ] && [ "$SUDO_USER" != "root" ]; then sudo -u "$SUDO_USER" "$@"; else "$@"; fi
}

# ── 1 — System dep: PortAudio (sounddevice needs libportaudio at runtime) ──
if ldconfig -p 2>/dev/null | grep -q libportaudio; then
    say "OK" "PortAudio already installed"
elif command -v apt-get >/dev/null 2>&1; then
    say ".." "Installing libportaudio2..."
    apt-get install -y libportaudio2
    say "OK" "PortAudio installed (libportaudio2)"
else
    say "WARN" "apt-get not found — install PortAudio manually (sounddevice needs libportaudio)"
fi

# ── 2 — Global plugin setup in ~/.config/opencode (COPY, not symlink) ──
GLOBAL_DIR="$USER_HOME/.config/opencode"
PLUGIN_DIR="$GLOBAL_DIR/opencode-rgbify-plugin"
PLUGINS_DIR="$GLOBAL_DIR/plugins"
LOADER="$PLUGINS_DIR/projector.ts"

run_as_user mkdir -p "$PLUGINS_DIR"

# Copy the plugin source into the global config so it keeps working even if
# the source repo is moved or deleted. .venv is machine-specific and created
# below; .git is not needed at runtime. If a previous install left a symlink,
# remove it first so the rm -rf below never follows it into the source repo.
if [ -L "$PLUGIN_DIR" ]; then
    run_as_user rm -f "$PLUGIN_DIR"
fi
run_as_user mkdir -p "$PLUGIN_DIR"
run_as_user rm -rf "$PLUGIN_DIR/src" "$PLUGIN_DIR/bridge"
run_as_user cp -r "$SCRIPT_DIR/src" "$SCRIPT_DIR/bridge" "$SCRIPT_DIR/package.json" "$PLUGIN_DIR/"
say "OK" "copied plugin to $PLUGIN_DIR"

run_as_user bash -s "$LOADER" <<'EOF'
cat > "$1" <<'LOADER'
export { RGBifyProjectorPlugin } from "../opencode-rgbify-plugin/src/index.ts"
LOADER
EOF
say "OK" "wrote $LOADER"

# ── 3 — Python deps (venv inside the plugin) ──
export PATH="$USER_HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    say "WARN" "uv not found — install bleak + sounddevice into the plugin venv manually"
else
run_as_user env "VENV_DIR=$PLUGIN_DIR/.venv" bash -c '
        export PATH="$HOME/.local/bin:$PATH"
        [ -x "$VENV_DIR/bin/python" ] || uv venv "$VENV_DIR"
        uv pip install --python "$VENV_DIR/bin/python" bleak sounddevice numpy
    ' && say "OK" "bridge deps installed (bleak + sounddevice + numpy)" || say "WARN" "bleak install failed"
fi

# ── 4 — Verify ──
# Run as the user with -B so the import never writes root-owned .pyc files
# into the user's venv (Python compiles bytecode cache on import).
if run_as_user "$PLUGIN_DIR/.venv/bin/python" -B -c 'import bleak, sounddevice' 2>/dev/null; then
    say "OK" "bridge deps importable (bleak + sounddevice)"
else
    say "WARN" "bridge deps not importable (RGBify bridge optional)"
fi
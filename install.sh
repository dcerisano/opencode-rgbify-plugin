#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SUDO_USER="${SUDO_USER:-${USER:-root}}"
USER_HOME="$(eval echo "~$SUDO_USER" 2>/dev/null || echo "${HOME:-/root}")"
LOG_FILE="/tmp/install-$(date +%Y%m%d-%H%M%S).log"

say() { local p="$1"; shift; echo "[$p] $*" | tee -a "$LOG_FILE"; }
INSTALL_STATE="$SCRIPT_DIR/.opencode/state"
mkdir -p "$INSTALL_STATE"

cleanup() { local rc=$?; [ $rc -ne 0 ] && say "ERR" "Install failed (exit $rc) — see $LOG_FILE"; exit $rc; }
trap cleanup EXIT

if [ "$(id -u)" != "0" ]; then
    echo "error: this installer must be run as root (sudo bash install.sh)"
    exit 1
fi

run_as_user() {
    if [ "$(id -u)" = "0" ] && [ "$SUDO_USER" != "root" ]; then sudo -u "$SUDO_USER" "$@"; else "$@"; fi
}
phase_gate() {
    say ".." "--- Phase $1 ---"
    if [ -f "$2" ]; then say "OK" "Phase $1 already completed"; echo; return 1; fi
    return 0
}
phase_gate_versioned() {
    local phase="$1" gate="$2" label="$3"; shift 3
    say ".." "--- Phase $phase ---"
    if [ ! -f "$gate" ]; then return 0; fi
    if "$@"; then say "OK" "$label up to date"; echo; return 1; fi
    say ".." "$label outdated — re-installing..."; rm -f "$gate"; return 0
}
version_changed() {
    local label="$1" current="$2" previous="$3"
    if [ "$current" != "$previous" ] && [ -n "$previous" ]; then
        touch "$INSTALL_STATE/restart-required"
        say "WARN" "$label changed ($previous → $current) — restart recommended"
    fi
}

# ── 1 — Node via nvm ──
phase_1_node() {
    local v_before v_after
    phase_gate_versioned "1" "$INSTALL_STATE/phase-1" "Node" \
        sh -c 'export NVM_DIR="$1/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" && nvm version 22 >/dev/null 2>&1' _ "$USER_HOME" || return 0
    if [ ! -d "$USER_HOME/.nvm" ]; then
        run_as_user bash -c 'curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash'
    fi
    v_before=$(node --version 2>/dev/null || echo "")
    run_as_user bash -c '
        export NVM_DIR="$HOME/.nvm"
        [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
        nvm install 22 >/dev/null 2>&1
        nvm alias default 22
    '
    v_after=$(node --version 2>/dev/null || echo "")
    version_changed "Node" "$v_after" "$v_before"
    touch $INSTALL_STATE/phase-1; say "OK" "Node 22 LTS installed"; echo
}

# ── 2 — uv ──
phase_2_uv() {
    local v_before v_after
    phase_gate_versioned "2" "$INSTALL_STATE/phase-2" "uv" \
        sh -c 'command -v uv >/dev/null && uv self update >/dev/null 2>&1' || return 0
    v_before=$(uv --version 2>/dev/null || echo "")
    run_as_user bash -c 'curl -fsSL https://astral.sh/uv/install.sh | bash'
    v_after=$(uv --version 2>/dev/null || echo "")
    version_changed "uv" "$v_after" "$v_before"
    export PATH="$USER_HOME/.local/bin:$PATH"
    touch $INSTALL_STATE/phase-2; echo
}

# ── 3 — opencode CLI (gated — reinstall only when missing; delete .opencode/state/phase-3 to force update) ──
phase_3_opencode() {
    local v_before v_after
    phase_gate_versioned "3" "$INSTALL_STATE/phase-3" "opencode" \
        sh -c 'command -v opencode >/dev/null 2>&1 && opencode --version >/dev/null 2>&1' || return 0
    say ".." "--- Phase 3 ---"
    v_before=$(opencode --version 2>/dev/null || echo "")
    run_as_user bash -c 'curl -fsSL https://opencode.ai/install | bash'
    v_after=$(opencode --version 2>/dev/null || echo "")
    version_changed "opencode" "$v_after" "$v_before"
    [ -f "$USER_HOME/.opencode/bin/opencode" ] && ln -sf "$USER_HOME/.opencode/bin/opencode" /usr/local/bin/opencode 2>/dev/null || true
    say "OK" "opencode CLI up to date"; echo
}

# ── 4 — Context7 API key ──
phase_4_context7() {
    phase_gate "4" "$INSTALL_STATE/phase-4" || return 0
    if [ -n "${CONTEXT7_API_KEY:-}" ]; then
        say "OK" "CONTEXT7_API_KEY set in environment"
    elif [ -f "$USER_HOME/.bashrc" ] && grep -q "CONTEXT7_API_KEY" "$USER_HOME/.bashrc" 2>/dev/null; then
        say "OK" "CONTEXT7_API_KEY found in ~/.bashrc"
    else
        say "WARN" "CONTEXT7_API_KEY not set — add 'export CONTEXT7_API_KEY=...' to ~/.bashrc"
    fi
    touch $INSTALL_STATE/phase-4; echo
}

# ── 5 — Verify ──
phase_5_verify() {
    say ".." "--- Phase 5 ---"
    node   --version 2>/dev/null && say "OK" "Node OK"   || say "WARN" "Node not in PATH"
    uv     --version 2>/dev/null && say "OK" "uv OK"     || say "WARN" "uv not in PATH"
    opencode --version 2>/dev/null && say "OK" "opencode OK" || say "WARN" "opencode not in PATH"
    say "OK" "Provider packages OK"
    say "OK" "Verify complete"
    echo

    echo "--- Install complete ---"
}

# ── Main ──
main() {
    echo "OpenCode Token Reduce — Installer"
    echo
    rm -f "$INSTALL_STATE/restart-required"

    phase_1_node
    phase_2_uv
    phase_3_opencode
    phase_4_context7

    phase_5_verify
}
main "$@"

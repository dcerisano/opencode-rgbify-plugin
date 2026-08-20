# opencode-rgbify-plugin (Serena project: opencode-token-reduce)

opencode plugin that streams chat text deltas to an RGBify 8x8 LED projector over BLE. GitHub: dcerisano/opencode-rgbify-plugin. v0.1.2, MIT.

## Structure
- `src/index.ts` — the opencode plugin (Bun/TS). Spawns the bridge, sanitizes text, forwards events.
- `bridge/ble_bridge.py` — Python BLE bridge (bleak + sounddevice + numpy). Reads newline-delimited text on stdin, writes to projector, auralizes on host.
- `install.sh` — root installer (Node/nvm, uv, opencode CLI, Context7 key). See `mem:installer`.
- `opencode.json` + `.opencode/` — opencode config, DCP plugin, `/migrate` command, memory-management skill. See `mem:opencode_config`.

## Key invariants
- **Interrupt semantics end-to-end**: every line is a new message that supersedes anything in flight. No buffering, no debounce, no replay. The last message is the only message.
- **Latest-line fan-out**: each sink (host auralizer, BLE) keeps only the most recent line (maxsize-1 queues, replace-on-full).
- **Char-perfect sync**: while connected, the host auralizer plays EXACTLY the bytes the projector receives (BLE loop pushes each written chunk back to host_line).
- **Always-on host auralizer**: sound continues even when projector is out of range; lines missed while down are dropped for the projector (no replay on reconnect).
- **Graceful shutdown on every death path**: SIGTERM/SIGINT/SIGHUP, stdin EOF, ppid watchdog, PR_SET_PDEATHSIG, 10s hard watchdog.

## Domains
- `mem:architecture` — plugin↔bridge data flow, event handling, spawn/respawn.
- `mem:bridge` — BLE specifics, UUIDs, constants, auralizer, volume persistence.
- `mem:conventions` — sanitize rules, MAX_TEXT, env vars, stdout protocol, commit style.
- `mem:tech_stack` — tooling and dependencies.
- `mem:installer` — install.sh phases and gates.
- `mem:opencode_config` — opencode.json, dcp.jsonc, /migrate command, serena setup.
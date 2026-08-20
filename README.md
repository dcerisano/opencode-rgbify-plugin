# opencode-rgbify-plugin

An [opencode](https://opencode.ai) plugin that streams chat text deltas to an
[RGBify](https://github.com/dcerisano/rgbify-projector) 8x8 LED projector over
BLE, with an always-on host auralizer so the sound never stops even when the
projector is out of range.

## How it works

```
opencode events ──► src/index.ts (plugin) ──► sanitize ──► line on stdin
                                                          │
                                              bridge/ble_bridge.py
                                              ├─► BLE write → RGBify projector
                                              └─► host auralizer (sounddevice)
```

- **`src/index.ts`** — the opencode plugin. Spawns the Python bridge, sanitizes
  every delta to printable ASCII (stripping control bytes and angle-bracket
  tags), and forwards events as newline-delimited lines.
- **`bridge/ble_bridge.py`** — the BLE bridge. Reads lines from stdin, chunks
  them to the negotiated MTU, writes them to the projector's `TEXT_BRIDGE`
  characteristic, and auralizes each char on the host at the firmware's native
  cadence (one ~33ms note per char, log-scale frequency table identical to the
  firmware).

### Interrupt semantics

Every event is delivered immediately as its own line — no buffering, no
debounce, no rate limiter. Each sink (BLE write path, host auralizer) keeps
only the **latest** line, so a newer message supersedes anything still in
flight. The last message is the only message.

While the projector is connected, the host auralizer plays **exactly** the
bytes the projector receives (char-perfect sync). While it's down, the host
keeps auralizing and lines are dropped for the projector — nothing is queued or
replayed on reconnect.

## Install

```bash
sudo bash install.sh
```

The installer (root-only) sets up Node 22 via nvm, uv, the opencode CLI, and
verifies the Context7 API key. It copies the plugin into the global opencode
config (`~/.config/opencode`). Phase gates live in `.opencode/state/`; delete a
gate file to force a reinstall.

The bridge runs in an isolated `.venv` (created on first run) with `bleak`,
`sounddevice`, and `numpy`. If `sounddevice`/`numpy` are unavailable, the host
auralizer is disabled and the projector path still works.

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `RGBIFY_DISABLE` | — | Set to `1`/`true` to disable the plugin |
| `RGBIFY_PROJECTOR_ADDR` | `40:91:51:AB:50:CE` | Fixed projector address (skips BLE discovery) |
| `RGBIFY_HOST_AURALIZER` | `1` | Set to `0` to disable the host auralizer |
| `RGBIFY_VOLUME` | `10` | Initial host volume (0–10) |
| `RGBIFY_STATE_DIR` | `~/.config/opencode/state` | Where `host-volume` is persisted |
| `RGBIFY_DEBUG_LOG` | — | Append debug log path |

Host volume is mirrored from the projector's `VOLUME` characteristic whenever
it's connected and persisted to `host-volume` so it survives restarts. While
the projector is off you can still adjust the host volume by editing that file.

## Development

```bash
npm install        # @opencode-ai/plugin types
bun run ...        # run opencode with the plugin loaded
```

The bridge logs one line per event to stdout for debugging: `ok <addr>` on
connect/delivery, `err <message>` on failure (it retries with backoff forever).

## License

MIT
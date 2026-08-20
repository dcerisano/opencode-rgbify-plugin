# BLE bridge (bridge/ble_bridge.py)

Reads newline-delimited text from stdin; streams to RGBify projector; auralizes on host. Reconnects forever with backoff.

## UUIDs / constants
- SERVICE `8bc01404-0000-4bf4-95d1-ce27a0477183`; TEXT_BRIDGE `...-0009-...`; VOLUME `...-0004-...`; DEVICE_NAME "RGBify Projector".
- MAX_BYTES=200, RECONNECT_DELAY=2.0, SCAN_TIMEOUT=5.0, CONNECT_SETTLE_MS=6.0 (firmware connect sound dialup_wav = 5.5s; first write held until it finishes), CONNECT_DELAY=1.0, IDLE_CHECK_MS=5.0.
- SAMPLE_RATE=44100, NOTE_SEC=1/30 (one ~33ms note per char, firmware cadence).

## MTU
- Bleak 3.x: `client._backend._acquire_mtu()` (public `_acquire_mtu` is off the wrapper — old call raised AttributeError, silently swallowed, leaving mtu=23 → 20-char cap). Firmware raises MTU to 517. `limit = min(MAX_BYTES, max(1, mtu - 3))`.

## Latest-line fan-out
- `host_line` and `ble_line` are `asyncio.Queue(maxsize=1)`; `push_latest()` replaces on full.
- `broadcast()`: connected → `ble_line` only; disconnected → `host_line` only (host keeps auralizing, nothing queued for replay).
- BLE loop pushes each successfully written chunk back to `host_line` → host plays exactly what projector received.
- Chunk loop breaks if `ble_line` non-empty or stop requested (interrupt semantics).

## Host auralizer
- sounddevice OutputStream int16 mono; one note at a time; `play_note` latest-wins (replaces pending note).
- `synth_note`: freq from `AURALIZER_FREQ` table (mirrors firmware `auralizer_freq[91]`, index = ord(c)-32 for ASCII 32..122 → 5000..100 Hz); whitespace/out-of-range = rest; peak = volume/10 * 32767 * 0.05.
- Volume-change chirp: 1970 Hz, 30ms, at current volume.
- Disabled if sounddevice/numpy import fails or `RGBIFY_HOST_AURALIZER=0`.

## Volume persistence
- On connect: read VOLUME char → `save_volume(value[0])`; subscribe `start_notify(VOLUME_UUID)` → save + chirp on change.
- State file: `~/.config/opencode/state/host-volume` (override `RGBIFY_STATE_DIR`); editable while projector off; `RGBIFY_VOLUME` = initial default. Clamped 0-10.

## Shutdown / resilience
- `arm_parent_death_signal`: Linux `ctypes prctl(1, SIGTERM)` (PR_SET_PDEATHSIG) on main thread; bail if ppid already changed. Non-Linux → ppid watchdog fallback.
- `wait_line_or_stop`: races queue vs stop_event so SIGTERM during idle wait is noticed immediately (not after IDLE_CHECK_MS).
- `watch_parent`: ppid change → graceful stop (stdin EOF can be held open by inherited pipe, e.g. MCP server).
- `on_disconnect`: clears both queues, runs `bluetoothctl disconnect <addr>` to force-clear BlueZ state.
- 10s hard watchdog: after stop, `os._exit(0)`.
- stdout protocol: `ok <addr>` / `ok` / `err <message>` (debugging only).
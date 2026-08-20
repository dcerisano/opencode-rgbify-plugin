# Architecture: plugin ↔ bridge

## Data flow
opencode events → `src/index.ts` plugin → sanitize → single line on bridge stdin → `bridge/ble_bridge.py` → BLE write to projector + host auralizer.

## src/index.ts (Bun/TS)
- `RGBifyProjectorPlugin` exported; returns `{}` early if `RGBIFY_DISABLE=1|true`.
- Bridge spawn: `spawn([python, BRIDGE], {stdin/out/err: pipe})`. Python resolved from `.venv/bin/python` (relative to plugin) else `which("python3") ?? which("python")`.
- Default projector addr `40:91:51:AB:50:CE` injected via env unless `RGBIFY_PROJECTOR_ADDR` set (skips ~5s BLE discovery).
- **Respawn**: `proc.exited.finally(() => procPromise = null)` — `exited` RESOLVES on exit (never rejects), so `.catch` would never fire. Next `send()` respawns.
- `send()`: `sanitize(text).slice(-MAX_TEXT)`; writes `line + "\n"` then **`proc.stdin.flush()`** (Bun FileSink buffers; without flush the bridge gets delayed multi-second bursts). Flush result may be a Promise — `.catch(()=>{})` it.
- `dispose`: kills bridge proc (immediate, explicit).

## Events handled
- `message.part.delta` — only `field === "text" | "reasoning"`; tool-part deltas carry internal tool-call JSON → garbage on projector, skipped.
- `session.next.text.delta` / `session.next.reasoning.delta` — `delta` string.
- `chat.message` — full `output.parts` of `type === "text"` (full-message path; MAX_TEXT guards this).
- `tool.execute.before` → `"tool IN"`; `tool.execute.after` → `"tool out"` (lowercase out).
- Unknown event types logged once via `seenEventTypes` set to `RGBIFY_DEBUG_LOG`.

## Debug
`RGBIFY_DEBUG_LOG` env → `appendFileSync` timestamped lines (send len, new event types).
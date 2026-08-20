# Conventions

## Text sanitization (src/index.ts)
- Keep ONLY printable ASCII `0x20..0x7e` (Atari font supports ~32-122). Drop control bytes, UTF-8/multibyte, and angle-bracket tags.
- `inTag` state is module-level and persists across `sanitize()` calls — tags stream in as multiple token deltas, so a split tag's tail (e.g. `-age-id>`) would leak through otherwise.
- `MAX_TEXT = 256` hard cap: `sanitize(text).slice(-MAX_TEXT)`. NOT a buffer — the LAST MAX_TEXT chars win. Guards the full-message path (`chat.message`); a single oversized write corrupts firmware LEDText scroll state.

## Interrupt semantics
- Every event delivered immediately as its own line. No accumulation, no debounce, no rate limiter. Newer line supersedes anything in flight. Last message is the only message.

## Env vars
- `RGBIFY_DISABLE=1|true` — disable plugin.
- `RGBIFY_PROJECTOR_ADDR` — fixed projector address (default `40:91:51:AB:50:CE`).
- `RGBIFY_HOST_AURALIZER=0` — disable host auralizer.
- `RGBIFY_VOLUME` — initial host volume (0-10, default 10).
- `RGBIFY_STATE_DIR` — override state dir (default `~/.config/opencode/state`).
- `RGBIFY_DEBUG_LOG` — append debug log path.

## Bridge stdout protocol
One line per event: `ok <addr>` (connected/delivered), `err <message>` (delivery failure, will retry). Debugging only.

## Commit style
Single-line imperative summaries, often with a leading domain tag (e.g. "Installer:", "Bridge:"). Author kronos/dcerisano. See git log for evolution narrative.
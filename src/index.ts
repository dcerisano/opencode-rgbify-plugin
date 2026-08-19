import type { Plugin } from "@opencode-ai/plugin"
import { spawn, which } from "bun"
import { existsSync } from "node:fs"
import { appendFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import path from "node:path"

const here = path.dirname(fileURLToPath(import.meta.url))
const BRIDGE = path.join(here, "..", "bridge", "ble_bridge.py")
const VENV_PYTHON = path.join(here, "..", ".venv", "bin", "python")
const DEBUG_LOG = process.env.RGBIFY_DEBUG_LOG

function debug(line: string) {
  if (!DEBUG_LOG) return
  try {
    appendFileSync(DEBUG_LOG, `${Date.now()} ${line}\n`)
  } catch {}
}

function resolvePython(): string | null {
  if (existsSync(VENV_PYTHON)) return VENV_PYTHON
  return which("python3") ?? which("python")
}

// Hard safety cap: a single oversized write corrupts the firmware's LEDText
// scroll state. The bridge chunks to the BLE MTU anyway; this only guards the
// full-message path (chat.message). Not a buffer — the LAST MAX_TEXT chars win.
const MAX_TEXT = 256

function isEnabled(): boolean {
  return process.env.RGBIFY_DISABLE !== "1" && process.env.RGBIFY_DISABLE !== "true"
}

// Keep ONLY printable ASCII (Atari font supports ~32-122). Drop control bytes,
// UTF-8/multibyte garbage, and any angle-bracket tag (e.g. dcp-message-id).
// Tags stream in as multiple token deltas, so tag-open state must persist
// across sanitize() calls — otherwise a split tag's tail (e.g. "-age-id>")
// would leak through.
let inTag = false

function sanitize(text: string): string {
  let out = ""
  for (const ch of text) {
    if (inTag) {
      if (ch === ">") inTag = false
      continue
    }
    if (ch === "<") {
      inTag = true
      continue
    }
    const code = ch.charCodeAt(0)
    if (code >= 0x20 && code <= 0x7e) out += ch
  }
  return out
}

export const RGBifyProjectorPlugin: Plugin = async ({ client }) => {
  if (!isEnabled()) return {}

  let procPromise: Promise<ReturnType<typeof spawn>> | null = null
  const seenEventTypes = new Set<string>()

  function startBridge(): Promise<ReturnType<typeof spawn>> {
    if (procPromise) return procPromise
    procPromise = (async () => {
      const python = resolvePython()
      if (!python) throw new Error("no python3/python interpreter found")
      const proc = spawn([python, BRIDGE], {
        stdin: "pipe",
        stdout: "pipe",
        stderr: "pipe",
        // Skip BLE discovery (a ~5s scan) on every connect: default to the
        // projector's fixed address unless the user overrides it.
        env: {
          ...process.env,
          RGBIFY_PROJECTOR_ADDR:
            process.env.RGBIFY_PROJECTOR_ADDR || "40:91:51:AB:50:CE",
        },
      })
      void proc.stdout?.pipeTo(new WritableStream({ write() {} }))
      void proc.stderr?.pipeTo(
        new WritableStream({ write() {} }),
      )
      // proc.exited RESOLVES (with the exit code) on exit — it does NOT reject,
      // so a `.catch` would never fire and the bridge would never respawn. Use
      // `.finally` so a dead bridge is replaced by the next send().
      void proc.exited.finally(() => {
        procPromise = null
      })
      return proc
    })()
    return procPromise
  }

  // Interrupt semantics: every event is delivered immediately as its own line.
  // No accumulation, no debounce timer, no rate limiter. The bridge keeps only
  // the latest line, so a newer event supersedes any still in flight — the last
  // message is the only message.
  function send(text: string) {
    const line = sanitize(text).slice(-MAX_TEXT)
    if (!line) return
    debug(`send len=${line.length}`)
    startBridge()
      .then((proc) => {
        proc.stdin.write(line + "\n")
      })
      .catch(async (err) => {
        await client.app.log({
          body: { service: "rgbify", level: "warn", message: `bridge unavailable: ${err}` },
        })
      })
  }

  function sendNow(text: string) {
    send(text)
  }

  startBridge().catch(async (err) => {
    await client.app.log({
      body: { service: "rgbify", level: "warn", message: `bridge unavailable: ${err}` },
    })
  })

  return {
    event: async ({ event }) => {
      const t = event.type
      if (!seenEventTypes.has(t)) {
        seenEventTypes.add(t)
        debug(`event type=${t}`)
      }

      // Token-level streaming deltas — fire per-token as the LLM streams,
      // before opencode renders the accumulated part. Small, so the projector
      // keeps up and stays in sync with the session.
      if (t === "message.part.delta") {
        const p = event.properties as { field?: string; delta?: string }
        // Only stream text/reasoning deltas. Tool-part deltas carry opencode
        // internal tool-call JSON (messageID/callID/...) that would render as
        // garbage on the projector; tool lifecycle is signalled separately.
        if (p.field === "text" || p.field === "reasoning") {
          if (typeof p.delta === "string" && p.delta) {
            send(p.delta)
          }
        }
        return
      }
      if (t === "session.next.text.delta" || t === "session.next.reasoning.delta") {
        const p = event.properties as { delta?: string }
        if (typeof p.delta === "string" && p.delta) {
          send(p.delta)
        }
        return
      }
    },
    "chat.message": async (_input, output) => {
      for (const part of output.parts) {
        if (part.type !== "text") continue
        sendNow(part.text)
      }
    },
    "tool.execute.before": async (input) => {
      send("tool IN")
    },
    "tool.execute.after": async () => {
      send("tool out")
    },
    // When opencode shuts down, kill the bridge so it doesn't linger as an
    // orphan holding the projector connection. Closing its stdin would also do
    // it (the bridge exits on EOF), but kill is immediate and explicit.
    dispose: async () => {
      if (procPromise) {
        try {
          const proc = await procPromise
          proc.kill()
        } catch {}
        procPromise = null
      }
    },
  }
}

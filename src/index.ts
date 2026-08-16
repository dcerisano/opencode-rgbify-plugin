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

const FLUSH_MS = 250
const RATE = Math.max(1, parseFloat(process.env.RGBIFY_CHARS_PER_SEC ?? "30") || 30)

const PREFIX_USER = process.env.RGBIFY_PREFIX_USER ?? "[U]"
const PREFIX_ASSISTANT = process.env.RGBIFY_PREFIX_ASSISTANT ?? "[A]"
const PREFIX_REASONING = process.env.RGBIFY_PREFIX_REASONING ?? "[R]"
const PREFIX_TOOL = process.env.RGBIFY_PREFIX_TOOL ?? "[T]"

function isEnabled(): boolean {
  return process.env.RGBIFY_DISABLE !== "1" && process.env.RGBIFY_DISABLE !== "true"
}

function prefixFor(kind: "user" | "assistant" | "reasoning" | "tool"): string {
  if (kind === "user") return PREFIX_USER
  if (kind === "assistant") return PREFIX_ASSISTANT
  if (kind === "reasoning") return PREFIX_REASONING
  return PREFIX_TOOL
}

export const RGBifyProjectorPlugin: Plugin = async ({ client }) => {
  if (!isEnabled()) return {}

  let procPromise: Promise<ReturnType<typeof spawn>> | null = null
  let pending = ""
  let flushTimer: ReturnType<typeof setTimeout> | null = null
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
        env: { ...process.env },
      })
      void proc.stdout?.pipeTo(
        new WritableStream({ write() {} }),
      )
      void proc.stderr?.pipeTo(
        new WritableStream({ write() {} }),
      )
      void proc.exited.catch(() => {
        procPromise = null
      })
      return proc
    })()
    return procPromise
  }

  function scheduleFlush() {
    if (flushTimer) return
    flushTimer = setTimeout(() => {
      flushTimer = null
      flush()
    }, FLUSH_MS)
  }

  function flush() {
    if (flushTimer) {
      clearTimeout(flushTimer)
      flushTimer = null
    }
    const line = pending
    pending = ""
    if (!line) return
    const budget = Math.max(1, Math.round((RATE * FLUSH_MS) / 1000))
    const clipped = line.slice(-budget)
    debug(`flush line=${line.length} sent=${clipped.length}`)
    startBridge()
      .then((proc) => {
        proc.stdin.write(clipped + "\n")
      })
      .catch(async (err) => {
        await client.app.log({
          body: { service: "rgbify", level: "warn", message: `bridge unavailable: ${err}` },
        })
      })
  }

  function send(text: string) {
    if (!text) return
    pending += text
    scheduleFlush()
  }

  function sendNow(text: string) {
    if (!text) return
    pending += text
    flush()
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
        if (typeof p.delta === "string" && p.delta) {
          send(prefixFor(p.field === "reasoning" ? "reasoning" : "assistant") + p.delta)
        }
        return
      }
      if (t === "session.next.text.delta" || t === "session.next.reasoning.delta") {
        const p = event.properties as { delta?: string }
        if (typeof p.delta === "string" && p.delta) {
          send(prefixFor(t === "session.next.reasoning.delta" ? "reasoning" : "assistant") + p.delta)
        }
        return
      }
    },
    "chat.message": async (_input, output) => {
      for (const part of output.parts) {
        if (part.type !== "text") continue
        sendNow(prefixFor("user") + part.text)
      }
    },
    "tool.execute.before": async (input) => {
      send(prefixFor("tool") + input.tool)
    },
    "tool.execute.after": async () => {
      send(prefixFor("tool") + "ok")
    },
  }
}

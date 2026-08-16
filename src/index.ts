import type { Plugin } from "@opencode-ai/plugin"
import { spawn, which } from "bun"
import { existsSync } from "node:fs"
import { fileURLToPath } from "node:url"
import path from "node:path"

const here = path.dirname(fileURLToPath(import.meta.url))
const BRIDGE = path.join(here, "..", "bridge", "ble_bridge.py")
const VENV_PYTHON = path.join(here, "..", ".venv", "bin", "python")

function resolvePython(): string | null {
  if (existsSync(VENV_PYTHON)) return VENV_PYTHON
  return which("python3") ?? which("python")
}

const FLUSH_MS = 250
const MAX_PENDING = 4000

const PREFIX_USER = process.env.RGBIFY_PREFIX_USER ?? ""
const PREFIX_ASSISTANT = process.env.RGBIFY_PREFIX_ASSISTANT ?? ""
const PREFIX_REASONING = process.env.RGBIFY_PREFIX_REASONING ?? ""

function isEnabled(): boolean {
  return process.env.RGBIFY_DISABLE !== "1" && process.env.RGBIFY_DISABLE !== "true"
}

function prefixFor(kind: "user" | "assistant" | "reasoning"): string {
  if (kind === "user") return PREFIX_USER
  if (kind === "assistant") return PREFIX_ASSISTANT
  return PREFIX_REASONING
}

type PartLike = { id: string; type: string; text: string }

export const RGBifyProjectorPlugin: Plugin = async ({ client }) => {
  if (!isEnabled()) return {}

  let procPromise: Promise<ReturnType<typeof spawn>> | null = null
  let pending = ""
  let flushTimer: ReturnType<typeof setTimeout> | null = null
  const sent = new Map<string, number>()

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

  function send(text: string) {
    if (!text) return
    pending += text
    if (pending.length >= MAX_PENDING) flush()
    else scheduleFlush()
  }

  function partText(part: PartLike, delta?: string): string {
    if (delta) {
      sent.set(part.id, (sent.get(part.id) ?? 0) + delta.length)
      return delta
    }
    const n = sent.get(part.id) ?? 0
    const next = part.text.slice(n)
    if (next) sent.set(part.id, n + next.length)
    return next
  }

  return {
    event: async ({ event }) => {
      if (event.type !== "message.part.updated") return
      const part = event.properties.part
      if (part.type !== "text" && part.type !== "reasoning") return
      const delta = event.properties.delta
      const text = partText(part, delta)
      if (!text) return
      send(prefixFor(part.type === "reasoning" ? "reasoning" : "assistant") + text)
    },
    "chat.message": async (_input, output) => {
      for (const part of output.parts) {
        if (part.type !== "text") continue
        send(prefixFor("user") + part.text)
      }
    },
  }
}

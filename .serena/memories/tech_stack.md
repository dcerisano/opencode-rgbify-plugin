# Tech stack

## Plugin (src/index.ts)
- Bun runtime (uses `bun` spawn, `which`; Bun FileSink stdin). `"type": "module"`, main `src/index.ts`.
- `@opencode-ai/plugin` peer dep (`*`), dev dep `^1.17.0`. Plugin object: `{ event, "chat.message", "tool.execute.before", "tool.execute.after", dispose }`.
- Node builtins: `node:fs` (existsSync, appendFileSync), `node:url` (fileURLToPath), `node:path`.

## Bridge (bridge/ble_bridge.py)
- Python 3, asyncio. Deps: `bleak` (BleakClient/BleakScanner), optional `sounddevice` + `numpy` (host auralizer; import failure tolerated).
- Runs in isolated `.venv` at plugin root (`.venv/bin/python`), fallback to system python3/python.

## Installer (install.sh)
- bash, root-only. Installs: Node 22 via nvm, uv, opencode CLI, Context7 key. See `mem:installer`.

## Config
- `opencode.json`: small_model `opencode/deepseek-v4-flash-free`, DCP plugin `@tarquinen/opencode-dcp@3.1.15`, serena MCP (`uvx -p 3.13 --from serena-agent==1.7.0 serena start-mcp-server --context=ide --project-from-cwd`), context7 MCP (remote, `CONTEXT7_API_KEY`), local-llm provider (`@ai-sdk/openai-compatible` → `http://localhost:11434/v1`, qwen3:8b / qwen2.5-coder:7b), default_agent build, lsp false.
- `.opencode/dcp.jsonc`: DCP enabled, compress mode range, maxContextLimit 70% / minContextLimit 30%, protectedTools `serena_*` + `context7_*`, dedup + purgeErrors strategies, turnProtection 2.
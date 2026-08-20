# opencode config & tooling

## opencode.json
- `small_model: opencode/deepseek-v4-flash-free`; `compaction.auto: false` (DCP handles pruning).
- Plugin: `@tarquinen/opencode-dcp@3.1.15`; custom `/dcp` command (context, stats, sweep, manual mode).
- Agent temps: plan 0.1/0.9, build 0.0/0.9, explore 0.1/0.9.
- MCP: serena (local, `uvx -p 3.13 --from serena-agent==1.7.0 serena start-mcp-server --context=ide --project-from-cwd --open-web-dashboard=False`), context7 (remote, `CONTEXT7_API_KEY` header).
- Permissions: edit=ask, grep/glob=allow, `git push`=ask.
- Provider `local-llm`: `@ai-sdk/openai-compatible`, baseURL `http://localhost:11434/v1`, models qwen3:8b and qwen2.5-coder:7b (both 32k context, tools enabled, textVerbosity low).
- `experimental.primary_tools: ["compress"]`; `default_agent: build`; `lsp: false`.

## .opencode/dcp.jsonc
- DCP enabled; manualMode off (automaticStrategies on); pruneNotification minimal/chat.
- compress: permission allow, mode range, maxContextLimit 70%, minContextLimit 30%, nudgeFrequency 3, iterationNudgeThreshold 15, summaryBuffer true, nudgeForce strong, protectedTools `serena_*` + `context7_*`.
- strategies: deduplication (protectedTools serena_*), purgeErrors (turns 4).
- turnProtection: enabled, turns 2. experimental.allowSubAgents true.

## /migrate command (.opencode/commands/migrate.md)
- Copies token-reduce template into an existing project: install.sh, opencode.json, .opencode/.gitignore, .opencode/dcp.jsonc, .opencode/commands/migrate.md, .opencode/skills/memory-management/SKILL.md, .serena/.gitignore, .serena/memories/.gitkeep, .serena/project.yml.
- Asks for target path (or uses arg); runs `cp --parents` as subagent; does NOT track/stage/commit.

## Serena
- Project name `opencode-token-reduce`; language_servers: [bash]; fixed_tools includes memory tools; ignore_all_files_in_gitignore true.
- `.serena/memories/` holds memories; `.serena/.gitignore` ignores cache.
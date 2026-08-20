---
description: OpenCode token-reduce template into an existing project
agent: build
---

Extract the path from the prompt (the argument after `/migrate`) and expand `~`. 
If the path is non-blank and `test -d "$path/.git"` succeeds, skip asking and proceed. 
Otherwise, ask the user for the target path using the `question` tool. Keep asking until a non-blank path pointing to a separate git repo is confirmed.
Once confirmed run this bash command as a subagent task:

```bash
src_root="$(git rev-parse --show-toplevel)" && \
cd "$src_root" && \
cp --parents \
  install.sh \
  opencode.json \
  .opencode/.gitignore \
  .opencode/dcp.jsonc \
  .opencode/commands/migrate.md \
  .opencode/skills/memory-management/SKILL.md \
  .serena/.gitignore \
  .serena/memories/.gitkeep \
  .serena/project.yml \
  "$confirmed_path"
```

Report: "Migrated N files to <path>."

Do not track, stage, commit, or push the copied files.

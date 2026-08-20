---
name: memory-management
description: Use after completing meaningful project changes (new features, refactors, config changes, dependency updates) to update Serena memories. Also use when creating, renaming, or checking memories for staleness.
---

# Memory Management

## When to update

After completing meaningful project changes (new features, refactors, config changes, dependency updates), update relevant Serena memories to reflect the new state. Do not update for trivial or one-off changes.

- `mem:core` — update if project purpose, structure, or key invariants changed
- `mem:tech_stack` — update if languages, tooling, or dependencies changed
- `mem:conventions` — update if new conventions were established
- `mem:suggested_commands` — update if custom commands or workflows changed
- `mem:task_completion` — update if verification steps changed

Create new memories for uncovered domains (e.g., `mem:architecture`, `mem:deployment`, `mem:testing`). Use topic folders to group related memories.

## Initial creation (startup)

When no memories exist (check via `serena_list_memories`), delegate initial memory creation to a subagent via the `task` tool (type `general`) to avoid tool-call spillage in the main session.

## Discovery model

- Progressive discovery through references, building a graph of memories.
- Agents are provided with the list of all memory names at startup.
- `mem:core` is the top-level entry point (graph root). It should reference other memories covering major domains, which in turn reference more specific ones.
- Use topics/folders to group related memories (e.g., `frontend/core`, `backend/debugging`).
- References use `mem:` prefix inside backticks: `` `mem:frontend/core` ``.
- Surrounding text should clearly indicate what the referenced memory covers.
- Memories themselves should not contain "when to read" information.

## Style

Dense agent notes, not prose docs. Prefer invariants, terse bullets. Avoid obvious context, rationale, and examples unless they prevent likely mistakes. Keep guidance durable and generalizable, not task-local.

## Add/update threshold

Only add or update memories with stable, non-obvious project conventions that avoid complex rediscovery. Do not add: quick-read facts; generic language/framework knowledge; one-off task notes; volatile line-level details; behavior likely to change soon.

## Maintenance actions

- Renaming memories: references update automatically via Serena's `rename_memory` tool.
- Checking for stale memories: call `serena memories check`.

---
description: Attach a file to this session's VibeContext, or to the global context with --global
argument-hint: <file path> [--global]
allowed-tools: Bash
---

Attach a file to VibeContext. Arguments: `$ARGUMENTS`

- Without `--global`, attach it to the current session. The VibeContext session id is in the SessionStart context ("VibeContext session id: ..."). Run:
  `"${CLAUDE_PLUGIN_ROOT}/bin/vibecontext" add "<file>" --session <session id>`
- With `--global`, run:
  `"${CLAUDE_PLUGIN_ROOT}/bin/vibecontext" add "<file>" --global`

Resolve a relative path against the current working directory. Report the result in one line: indexed with N chunks, already added, or the error message.

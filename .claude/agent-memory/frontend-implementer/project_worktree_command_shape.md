---
name: worktree-command-shape
description: A worktree-isolated agent's Bash is refused when a command uses shell variables in argument position or nested constructs; write scripts to the scratchpad and call them with literal paths.
metadata:
  type: project
---

In a `.claude/worktrees/agent-*` session the harness refuses any Bash command
it cannot prove stays inside the worktree: `F=file; sed -n 1,5p $F`,
`for x in ...; do sed -n "${a},${b}p" ...`, `$(git ls-files ...)`, heredoc +
`cd` + git in one line. Each refusal costs a round trip.

**Why:** the isolation check treats any runtime-computed argument as possibly
being `git` or a path outside the worktree.

**How to apply:**
- Put multi-step logic in a node/python script written with the Write tool to
  the scratchpad, then run `node <abs-script> <abs-worktree-path>` plainly.
  TypeScript-AST codemods/dumps (`require('<frontend>/node_modules/typescript')`)
  worked well for call-site rewrites and pure-move proofs.
- Use literal absolute paths; split `git` commands from other work.
- zsh: `echo ======` fails (`=` expansion) — use `echo "-----"`; `$pipestatus`
  not `$PIPESTATUS`.
- perl `-pi` is line-by-line: a pattern ending in `\n` deletes the matched
  line's newline and silently merges lines; use `-0pi` for multi-line edits and
  never interpolate `${...}` template literals inside a perl replacement.

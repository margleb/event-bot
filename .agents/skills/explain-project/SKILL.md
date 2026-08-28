---
name: explain-project
description: Explain a focused concept, symbol, file, subsystem, or business/runtime flow as it is implemented in the current repository, using source-backed execution tracing, a concrete running example, a guided code tour, and an optional interactive HTML lesson. Use when the user asks how something works in this project or wants to learn a project concept. Do not use for explaining a diff or PR (use explain-diff) or for mapping the whole repository (use teachme).
---

# Explain Project

Teach one focused part of the current repository. Build the explanation from
verified code, not from a generic description of the technology.

This skill explains existing behavior. It does not modify code, perform a code
review, or claim that a suspicious pattern is a confirmed bug unless the user
separately asks for that work.

## Help

If the arguments are exactly `help`, `--help`, `-h`, or `?`, print this block
verbatim and stop without inspecting the repository:

```text
explain-project — teach how something works in the current repository

Usage:
  /explain-project [--html] <concept, symbol, path, or question>

Targets:
  concept     "semantic search", "dependency injection"
  symbol      SearchService.search, EventRepository
  path        src/search.py, src/bot/
  flow        "how an incoming event reaches the database"

Flags:
  --html      Also create an interactive lesson under
              <project-root>/tmp/<creator>-YYYY-MM-DD-explanation-<slug>.html

Examples:
  /explain-project "how semantic search works"
  /explain-project SearchService.search
  /explain-project "event processing from Telegram to storage" --html

Use /explain-diff teach-me for code changes and /teachme for a broad,
resumable map of the entire repository.
```

## Parse the request

- `--html` may appear anywhere. Everything else is the topic or target.
- The target may be a concept, symbol, path, subsystem, business rule, or an
  end-to-end question.
- Preserve the user's requested scope. If it is broad, choose one representative
  execution path, state that scope explicitly, and offer to explore other paths.
- If no target was supplied, ask what the user wants to understand and stop.

## Investigate before explaining

1. Locate the target with repository search. Prefer `rg` and `rg --files`; do
   not assume a similarly named file is the implementation.
2. Read the complete enclosing functions, classes, and configuration around
   relevant matches. A matching line alone is not enough evidence.
3. Trace the useful execution chain in both directions:
   - entry points and callers that provide data;
   - the central decisions and transformations;
   - callees, persistence, messages, side effects, and returned results.
4. Inspect relevant types, schemas, configuration, tests, and error handling.
   Use tests as evidence of intended examples, not as proof of every behavior.
5. Existing `.teachme` artifacts may accelerate discovery, but they can be stale.
   Verify their claims against the current source before using them.
6. Group findings into a small number of teaching themes ordered by conceptual
   dependency, not by filename.

If the requested concept is not present in the repository, say exactly what was
searched and that no project implementation was found. Do not silently replace
the request with a generic tutorial. Offer a generic explanation separately.

## Build the lesson

Read [references/output-format.md](references/output-format.md) before writing.

- Match the user's language while preserving code identifiers verbatim.
- Start concrete, then introduce abstractions. Define jargon briefly at first use.
- Choose one small running example with realistic toy data and reuse it through
  the explanation, execution trace, edge cases, and quiz.
- Cite project claims as `path/to/file:line` and quote only small code excerpts.
- Distinguish verified behavior from inference or likely intent.
- Describe uncertainty and alternative branches instead of inventing a single
  clean flow when the code has several.
- Explain potential failure modes as teaching points. Do not present them as
  verified bugs without a dedicated review.

## HTML output

When `--html` is present, provide the normal Markdown explanation and also write
a self-contained interactive HTML lesson according to
[references/output-format.md](references/output-format.md).

Resolve the project root with `git rev-parse --show-toplevel`; if the current
directory is not in a Git repository, use the current working directory. Save
the lesson at:

```text
<project-root>/tmp/<creator>-YYYY-MM-DD-explanation-<short-topic-slug>.html
```

Set `<creator>` to `codex` when running in OpenAI Codex and `claude` when
running in Claude Code. Create the `tmp` directory when necessary. At
completion, report both the project-relative path and the absolute Linux path
so the file is easy to find from WSL or another host environment.

## Honesty and stopping rules

- Every behavioral claim must be supported by current project code or be marked
  explicitly as inference.
- Never fabricate a caller, data flow, configuration value, or business reason.
- If the evidence ends at an external service or dependency, identify that
  boundary rather than speculating about its internals.
- If the target is generated, vendored, or extremely broad, explain at the
  highest useful verified level and state what was intentionally omitted.

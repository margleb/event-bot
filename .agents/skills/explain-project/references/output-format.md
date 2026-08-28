# Explain Project output format

Use the following structure as a teaching contract, adapting section length to
the topic. Do not add content merely to fill a heading.

## Markdown lesson

### 1. What you will understand

State the question being answered, the chosen scope, and the short answer in a
few sentences. Mark a narrowed scope explicitly.

### 2. Background

Provide two layers:

- **Broad context (skip if you know the project):** what the surrounding
  subsystem is responsible for and where it sits in the application.
- **Local context:** the specific symbols, state, data formats, or business rules
  required to follow this topic.

Introduce a concrete running example with realistic toy values. Say that the
values are illustrative when they are not copied from a fixture or test.

### 3. Core idea

Explain the central mechanism without code first. Show the input, important
decision or transformation, and outcome using the running example. A compact
table is preferred when it makes states or mappings easier to compare.

### 4. Execution path

Trace the representative path from entry to outcome. For each step provide:

- what receives control or data;
- what it decides or transforms;
- the relevant `file:line` location;
- what is passed to the next step.

Call out conditional branches that materially change the result. Do not imply
that a representative path is the only path.

### 5. Guided code tour

Walk through the implementation in conceptual order. Quote only the few lines
needed to teach each point. Explain why the code exists and connect every stop
back to the running example.

### 6. Boundaries and failure modes

Explain validation, empty data, exceptions, retries, concurrency, external
services, and other edges only when supported by the implementation. For each
important edge, show the triggering state and resulting behavior. Potential
risks are attention pointers, not confirmed bugs.

### 7. Mental model

End the teaching portion with a concise model the reader can retain: three to
seven ordered statements describing how the pieces fit together.

### 8. Quiz

Write five medium-difficulty multiple-choice questions with four plausible
options each. Test understanding of the mechanism and execution path, not trivia
such as line numbers. Hide each answer in:

```html
<details><summary>Answer</summary>
Explain why the correct option is right and why the most tempting wrong option
is wrong.
</details>
```

## HTML lesson (`--html`)

Create one self-contained HTML file with inline CSS and JavaScript and no
external resources.

- Use one long responsive page with a sticky or top-anchored table of contents.
- Include the same substantive sections as the Markdown lesson.
- Use one or two consistent visual forms, such as data-flow boxes and state
  tables. Put concrete example values on arrows or transitions.
- Build diagrams with semantic HTML and CSS. Do not use ASCII art.
- Render code with `<pre><code>` or ensure any custom code container has
  `white-space: pre` or `pre-wrap`.
- Render the quiz as interactive multiple choice. Clicking an option must show
  whether it is correct and explain that specific option. Use plain JavaScript.
- Visually distinguish definitions, verified behavior, inference, and warnings.
- Before saving, verify that navigation links, code formatting, and all quiz
  answers work without network access.

The filename is
`<project-root>/tmp/<creator>-YYYY-MM-DD-explanation-<short-topic-slug>.html`.
Set `<creator>` to `codex` in OpenAI Codex and `claude` in Claude Code. Resolve
the project root with `git rev-parse --show-toplevel`; if unavailable, use the
current working directory. Create `tmp/` when needed, then report the file's
project-relative and absolute paths after creation.

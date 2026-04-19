# Token Syntax

VegaNotes is markdown — **plain `.md` files are the source of truth**. On top of
standard markdown, a small set of inline tokens gives lines structured meaning.
The parser is a pure function: `parse(md) → {tasks, attrs, refs}`. UI mutations
(drag-and-drop, status change, edit-as-card) modify the AST and re-serialize
back into the file.

## Token grammar

| Token             | Meaning                                                   | Example                          |
| ----------------- | --------------------------------------------------------- | -------------------------------- |
| `!task <title>`   | Declares a task. Title runs until the next known `#token` | `- !task Wire up auth #eta +2d`  |
| `#task <slug>`    | Reference to an existing task → directed link             | `Blocked by #task auth-init`     |
| `#eta <when>`     | Due date (ISO, `+2d`, `tomorrow`, `next fri`)             | `#eta 2026-05-01`                |
| `#priority <val>` | Priority in user vocabulary (P0..P3, high/med/low)        | `#priority P1`                   |
| `#project <name>` | Project bucket                                            | `#project veganotes`             |
| `#owner <name>`   | Owner — repeat for multiple                               | `#owner alice #owner bob`        |
| `#status <val>`   | `todo` \| `in-progress` \| `done` \| `blocked`            | `#status done`                   |
| `#estimate <dur>` | Effort (`30m`, `2h`, `1d`, `0.5w`)                        | `#estimate 4h`                   |
| `#feature <name>` | Cross-cutting feature label                               | `#feature search-rewrite`        |
| `#link <slug>`    | Generic bidirectional link                                | `#link rfc-2026-04`              |

## Rules

1. A token attaches to the **nearest enclosing `!task`** — same line or the most
   recent `!task` above in the same list block.
2. Indentation: every 2 spaces (or one tab = 4 spaces) increases nesting level.
   Children become subtasks of their parent.
3. A blank line ends the current task block.
4. Unknown `#foo bar` tokens are preserved verbatim as free-form attributes
   (round-tripped on re-serialize).
5. `!task` titles are read until the next *known* `#token`, so you can write
   natural English titles without escaping.

## Worked example

```markdown
# Sprint 14

- !task Wire up SSO #project veganotes #owner alice #eta +5d #priority P1
  - !task Add login screen #owner bob #eta +6d
  - !task Add OAuth callback #owner alice #eta +7d #status in-progress
- !task Migrate index #feature search-rewrite #owner alice #status done
  Blocked by #task wire-up-sso and tracked in #link rfc-2026-04
```

This produces:
- 4 tasks (`wire-up-sso` + 2 children, plus `migrate-index`)
- `migrate-index` has `direction='out'` links to `wire-up-sso` and `rfc-2026-04`
- `wire-up-sso` automatically has a `direction='in'` link from `migrate-index`
  via the `links_bidir` view.

## Adding a new token

1. Add it to `backend/app/parser/tokens.py` and `packages/parser-ts/src/tokens.ts`.
2. Add an example to `backend/tests/fixtures/sprint14.md`; update `sprint14.json`.
3. `pytest` and `node --test` will fail until both implementations match.

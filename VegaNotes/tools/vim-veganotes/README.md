# vim-veganotes

A Vim filetype plugin that colorizes VegaNotes markdown files with the **same
palette and token rules** as the in-app editor
(`frontend/src/components/Editor/NoteEditor.tsx`).

## What it highlights

| Token            | Example                       | Color (web ↔ vim)            |
| ---------------- | ----------------------------- | ---------------------------- |
| Heading          | `## Sprint 14`                | slate-900 bold               |
| `!task`          | `- !task triage CRs`          | emerald-700 bold             |
| `!AR`            | `  - !AR ping owner`          | amber-700 bold               |
| `@user`          | `@namratha`, `@aboli`         | violet-700 bold              |
| `#attr` key      | `#project gfc`, `#eta ww17`   | sky-700 bold                 |
| status values    | `done`, `wip`, `in-progress`  | green / amber / rose italic  |
| priority values  | `p0`, `p1`, `high`, `low`     | rose bold                    |
| eta values       | `ww16.5`, `ww17`              | cyan italic                  |

Same indent-scoped semantics that the parser uses are *not* enforced by Vim —
this plugin is purely cosmetic.

## Install

### Native packages (Vim 8+/Neovim)

```sh
mkdir -p ~/.vim/pack/veganotes/start
ln -s /path/to/VegaNotes/tools/vim-veganotes \
      ~/.vim/pack/veganotes/start/vim-veganotes
```

For Neovim use `~/.config/nvim/pack/...` instead.

### vim-plug

```vim
Plug '/path/to/VegaNotes/tools/vim-veganotes'
```

## Auto-detection

Files are treated as `filetype=veganotes` when:

* Path matches `*/notes/*.md` (covers `.devdata/notes/`, `~/notes/`, etc.)
* Extension is `.veganotes` or `.vnote`
* A `.md` file contains `!task` or `!AR` in its first 50 lines

To force the filetype on the current buffer: `:setfiletype veganotes`.

Plain `.md` files that don't match still get the four core tokens via
`after/syntax/markdown.vim` (no full color theme, just hints).

## Mappings (in `veganotes` buffers)

| Mapping | Action                  |
| ------- | ----------------------- |
| `]t`    | jump to next `!task`    |
| `[t`    | jump to previous `!task`|
| `]a`    | jump to next `!AR`      |
| `[a`    | jump to previous `!AR`  |

## Editing from VegaNotes

The web app has an "Edit in vim" affordance — it writes the buffer back to
disk and the indexer rescans on save. With this plugin installed, the vim
session looks visually identical to the in-app editor.

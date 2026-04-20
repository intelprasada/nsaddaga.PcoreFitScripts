" Vim syntax file for VegaNotes markdown
" Maintainer: VegaNotes
" Mirrors the in-app editor highlighter (frontend/src/components/Editor/NoteEditor.tsx)

if exists("b:current_syntax")
  finish
endif

" --- Base markdown bits we care about -------------------------------------
" Headings: leading #'s then heading text
syntax match vegaHeadingMarker  /^\s*#\{1,6}\ze\s/
syntax match vegaHeading        /^\s*#\{1,6}\s\+.*$/ contains=vegaHeadingMarker,vegaTask,vegaAR,vegaUser,vegaAttr,vegaAttrKV

" Bullet markers (-, *, +) and list indentation
syntax match vegaBullet         /^\s*[-*+]\s/

" Fenced code blocks (skip our highlighting inside)
syntax region vegaCode          start=/^\s*```/ end=/^\s*```/ keepend

" --- VegaNotes special tokens ---------------------------------------------
" !task and !AR literals
syntax match vegaTask           /!task\>/
syntax match vegaAR             /!AR\>/

" @user references (allow letters, digits, dot, dash, underscore)
syntax match vegaUser           /\%(^\|[[:space:](\[]\)\zs@[A-Za-z][A-Za-z0-9._-]*/

" #attr (#project, #eta, #priority, #status, ...). Must NOT match inside a
" word and must NOT match a heading marker (handled by ordering / context).
" The lookbehind \%(^\|[^A-Za-z0-9_-]\) ensures it isn't in the middle of
" an identifier.
syntax match vegaAttrKV         /\%(^\|[^A-Za-z0-9_#-]\)\zs#[A-Za-z][A-Za-z0-9_-]*\%(\s\+\S\+\)\?/ contains=vegaAttrKey,vegaAttrVal,vegaStatusDone,vegaStatusWip,vegaStatusBlocked,vegaPriority,vegaEta
syntax match vegaAttrKey        /#[A-Za-z][A-Za-z0-9_-]*/ contained
syntax match vegaAttrVal        /\s\+\zs\S\+/ contained

" Status value coloring (done / wip / in-progress / blocked / pending)
syntax match vegaStatusDone     /\<done\>/             contained
syntax match vegaStatusWip      /\<\%(wip\|in-progress\|in_progress\|in progress\)\>/ contained
syntax match vegaStatusBlocked  /\<blocked\>/          contained
syntax match vegaPriority       /\<\%(p[0-3]\|high\|med\|medium\|low\)\>/ contained
syntax match vegaEta            /\<ww[0-9]\+\%(\.[0-9]\+\)\?\>/ contained

" Bare #attr without value (still color the key)
syntax match vegaAttr           /\%(^\|[^A-Za-z0-9_#-]\)\zs#[A-Za-z][A-Za-z0-9_-]*\>/

" --- Highlight links (truecolor + 256-color fallbacks) --------------------
" Palette mirrors the Tailwind colors used by the web editor:
"   emerald-700 #047857  amber-700 #b45309  sky-700 #0369a1
"   violet-700 #6d28d9   slate-900 #0f172a  rose-600 #e11d48

hi default vegaHeading       guifg=#0f172a gui=bold ctermfg=White cterm=bold
hi default vegaHeadingMarker guifg=#64748b gui=bold ctermfg=Gray  cterm=bold
hi default vegaBullet        guifg=#64748b               ctermfg=Gray
hi default vegaTask          guifg=#047857 gui=bold ctermfg=DarkGreen cterm=bold
hi default vegaAR            guifg=#b45309 gui=bold ctermfg=DarkYellow cterm=bold
hi default vegaUser          guifg=#6d28d9 gui=bold ctermfg=Magenta    cterm=bold
hi default vegaAttr          guifg=#0369a1 gui=bold ctermfg=Cyan       cterm=bold
hi default vegaAttrKey       guifg=#0369a1 gui=bold ctermfg=Cyan       cterm=bold
hi default vegaAttrVal       guifg=#0e7490               ctermfg=DarkCyan
hi default vegaStatusDone    guifg=#047857 gui=italic ctermfg=DarkGreen cterm=italic
hi default vegaStatusWip     guifg=#b45309 gui=italic ctermfg=DarkYellow cterm=italic
hi default vegaStatusBlocked guifg=#e11d48 gui=italic ctermfg=Red        cterm=italic
hi default vegaPriority      guifg=#be123c gui=bold   ctermfg=Red        cterm=bold
hi default vegaEta           guifg=#0e7490 gui=italic ctermfg=DarkCyan   cterm=italic
hi default link vegaCode     Comment

let b:current_syntax = "veganotes"

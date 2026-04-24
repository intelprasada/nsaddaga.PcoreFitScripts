" Vim syntax file for VegaNotes markdown
" Maintainer: VegaNotes
" Mirrors the in-app editor highlighter (frontend/src/components/Editor/NoteEditor.tsx)

if exists("b:current_syntax")
  finish
endif

" --- Base markdown bits we care about -------------------------------------
" Headings: leading #'s then heading text
syntax match vegaHeadingMarker  /^\s*#\{1,6}\ze\s/
syntax match vegaHeading        /^\s*#\{1,6}\s\+.*$/ contains=vegaHeadingMarker,vegaTask,vegaAR,vegaUser,vegaAttr,vegaRefTask,vegaRefAR,vegaTaskId,vegaEtaKV,vegaStatusKV,vegaPriorityKV

" Bullet markers (-, *, +) and list indentation
syntax match vegaBullet         /^\s*[-*+]\s/

" Fenced code blocks (skip our highlighting inside)
syntax region vegaCode          start=/^\s*```/ end=/^\s*```/ keepend

" --- VegaNotes special tokens ---------------------------------------------
" !task and !AR declaration keywords
syntax match vegaTask           /!task\>/
syntax match vegaAR             /!AR\>/

" @user references (allow letters, digits, dot, dash, underscore)
syntax match vegaUser           /\%(^\|[[:space:](\[]\)\zs@[A-Za-z][A-Za-z0-9._-]*/

" Generic #attr fallback — more specific matchers below override this at the
" same position because they are defined later (Vim: last match wins).
syntax match vegaAttr           /\%(^\|[^A-Za-z0-9_#-]\)\zs#[A-Za-z][A-Za-z0-9_-]*\>/

" Task ID token: #id T-XXXX (indigo monospace chip)
syntax match vegaTaskId         /\%(^\|[^A-Za-z0-9_#-]\)\zs#id\s\+T-[A-Z0-9]\+/

" Ref-row keywords: #task [T-XXXX] → same emerald as !task
"                   #AR   [T-XXXX] → same amber   as !AR
syntax match vegaRefTask        /\%(^\|[^A-Za-z0-9_#-]\)\zs#task\%(\s\+T-[A-Z0-9]\+\)\?\>/
syntax match vegaRefAR          /\%(^\|[^A-Za-z0-9_#-]\)\zs#AR\%(\s\+T-[A-Z0-9]\+\)\?\>/

" Value-bearing attrs: keyword + next word as one styled unit.
"   #eta      <value>  → rose    (mirrors vega-eta chip)
"   #status   <value>  → emerald (mirrors vega-status chip)
"   #priority <value>  → amber   (mirrors vega-priority chip)
syntax match vegaEtaKV          /\%(^\|[^A-Za-z0-9_#-]\)\zs#eta\s\+\S\+/
syntax match vegaStatusKV       /\%(^\|[^A-Za-z0-9_#-]\)\zs#status\s\+\S\+/
syntax match vegaPriorityKV     /\%(^\|[^A-Za-z0-9_#-]\)\zs#priority\s\+\S\+/

" --- Highlight links (truecolor + 256-color fallbacks) --------------------
" Palette mirrors the Tailwind colors used by the web editor:
"   emerald-700 #047857  amber-700 #b45309  sky-700    #0369a1
"   violet-700  #6d28d9  slate-900 #0f172a  rose-700   #be123c
"   indigo-600  #4f46e5

hi default vegaHeading       guifg=#0f172a gui=bold        ctermfg=White      cterm=bold
hi default vegaHeadingMarker guifg=#64748b gui=bold        ctermfg=Gray       cterm=bold
hi default vegaBullet        guifg=#64748b                 ctermfg=Gray
hi default vegaTask          guifg=#047857 gui=bold        ctermfg=DarkGreen  cterm=bold
hi default vegaAR            guifg=#b45309 gui=bold        ctermfg=DarkYellow cterm=bold
hi default vegaUser          guifg=#6d28d9 gui=bold        ctermfg=Magenta    cterm=bold
hi default vegaAttr          guifg=#0369a1 gui=bold        ctermfg=Cyan       cterm=bold
" Specific value-bearing attr tokens
hi default vegaTaskId        guifg=#4f46e5 gui=bold        ctermfg=Blue       cterm=bold
hi default vegaRefTask       guifg=#047857 gui=bold        ctermfg=DarkGreen  cterm=bold
hi default vegaRefAR         guifg=#b45309 gui=bold        ctermfg=DarkYellow cterm=bold
hi default vegaEtaKV         guifg=#be123c gui=bold        ctermfg=Red        cterm=bold
hi default vegaStatusKV      guifg=#047857 gui=bold        ctermfg=DarkGreen  cterm=bold
hi default vegaPriorityKV    guifg=#b45309 gui=bold        ctermfg=DarkYellow cterm=bold
hi default link vegaCode     Comment

let b:current_syntax = "veganotes"

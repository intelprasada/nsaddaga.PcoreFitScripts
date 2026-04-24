" Augment the built-in markdown syntax with VegaNotes tokens, so plain .md
" files (not detected as veganotes) still get colored hints.
" Mirrors frontend/src/components/Editor/NoteEditor.tsx highlighter.

" Declaration keywords
syntax match vegaTask     /!task\>/                                                                     containedin=ALL
syntax match vegaAR       /!AR\>/                                                                       containedin=ALL

" @user references
syntax match vegaUser     /\%(^\|[[:space:](\[]\)\zs@[A-Za-z][A-Za-z0-9._-]*/                          containedin=ALL

" Generic #attr fallback (specific matchers below win at same position)
syntax match vegaAttr     /\%(^\|[^A-Za-z0-9_#-]\)\zs#[A-Za-z][A-Za-z0-9_-]*\>/                       containedin=ALL

" Task ID chip: #id T-XXXX → indigo
syntax match vegaTaskId   /\%(^\|[^A-Za-z0-9_#-]\)\zs#id\s\+T-[A-Z0-9]\+/                             containedin=ALL

" Ref-row keywords with optional UUID: #task [T-XXXX] / #AR [T-XXXX]
syntax match vegaRefTask  /\%(^\|[^A-Za-z0-9_#-]\)\zs#task\%(\s\+T-[A-Z0-9]\+\)\?\>/                  containedin=ALL
syntax match vegaRefAR    /\%(^\|[^A-Za-z0-9_#-]\)\zs#AR\%(\s\+T-[A-Z0-9]\+\)\?\>/                    containedin=ALL

" Value-bearing attrs: keyword + next word as one token
syntax match vegaEtaKV      /\%(^\|[^A-Za-z0-9_#-]\)\zs#eta\s\+\S\+/                                  containedin=ALL
syntax match vegaStatusKV   /\%(^\|[^A-Za-z0-9_#-]\)\zs#status\s\+\S\+/                               containedin=ALL
syntax match vegaPriorityKV /\%(^\|[^A-Za-z0-9_#-]\)\zs#priority\s\+\S\+/                             containedin=ALL

hi default vegaTask        guifg=#047857 gui=bold        ctermfg=DarkGreen  cterm=bold
hi default vegaAR          guifg=#b45309 gui=bold        ctermfg=DarkYellow cterm=bold
hi default vegaUser        guifg=#6d28d9 gui=bold        ctermfg=Magenta    cterm=bold
hi default vegaAttr        guifg=#0369a1 gui=bold        ctermfg=Cyan       cterm=bold
hi default vegaTaskId      guifg=#4f46e5 gui=bold        ctermfg=Blue       cterm=bold
hi default vegaRefTask     guifg=#047857 gui=bold        ctermfg=DarkGreen  cterm=bold
hi default vegaRefAR       guifg=#b45309 gui=bold        ctermfg=DarkYellow cterm=bold
hi default vegaEtaKV       guifg=#be123c gui=bold        ctermfg=Red        cterm=bold
hi default vegaStatusKV    guifg=#047857 gui=bold        ctermfg=DarkGreen  cterm=bold
hi default vegaPriorityKV  guifg=#b45309 gui=bold        ctermfg=DarkYellow cterm=bold

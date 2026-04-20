" Augment the built-in markdown syntax with VegaNotes tokens, so plain .md
" files (not detected as veganotes) still get colored hints.

syntax match vegaTask    /!task\>/                                                            containedin=ALL
syntax match vegaAR      /!AR\>/                                                              containedin=ALL
syntax match vegaUser    /\%(^\|[[:space:](\[]\)\zs@[A-Za-z][A-Za-z0-9._-]*/                  containedin=ALL
syntax match vegaAttr    /\%(^\|[^A-Za-z0-9_#-]\)\zs#[A-Za-z][A-Za-z0-9_-]*\>/                containedin=ALL

hi default vegaTask  guifg=#047857 gui=bold ctermfg=DarkGreen  cterm=bold
hi default vegaAR    guifg=#b45309 gui=bold ctermfg=DarkYellow cterm=bold
hi default vegaUser  guifg=#6d28d9 gui=bold ctermfg=Magenta    cterm=bold
hi default vegaAttr  guifg=#0369a1 gui=bold ctermfg=Cyan       cterm=bold

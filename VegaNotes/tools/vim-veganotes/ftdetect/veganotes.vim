" VegaNotes filetype detection.
"
" Strategy: we run on multiple events to guarantee we win over the default
" `*.md → markdown` rule, regardless of vimrc plugin/colorscheme ordering.
"
"   - BufRead/BufNewFile : early path-based and content-based detection.
"   - FileType markdown   : re-promote any markdown buffer that is actually a
"                           VegaNotes note (path under */notes/* or content
"                           contains !task / !AR).
"   - BufWinEnter        : last-resort re-check after all syntax loading.

augroup veganotes_detect
  autocmd!
  autocmd BufRead,BufNewFile *.veganotes,*.vnote     set filetype=veganotes
  " Match notes/foo.md, notes/sub/foo.md, notes/a/b/c.md, ... (vim's `*` does
  " not cross `/`, so we enumerate depths plus use `**` as a belt-and-braces
  " catch on Vim 8+).
  autocmd BufRead,BufNewFile
        \ */notes/*.md,
        \*/notes/*/*.md,
        \*/notes/*/*/*.md,
        \*/notes/*/*/*/*.md,
        \*/notes/**/*.md
        \ set filetype=veganotes
  autocmd BufRead     *.md     call s:VegaNotesSniff()
  autocmd FileType    markdown call s:VegaNotesPromote()
  autocmd BufWinEnter *.md     call s:VegaNotesPromote()
augroup END

function! s:IsVegaPath(name) abort
  return a:name =~# '\v[\\/]notes[\\/]' || a:name =~# '\.\(veganotes\|vnote\)$'
endfunction

function! s:HasVegaMarkers() abort
  let l:n = min([80, line('$')])
  for l:i in range(1, l:n)
    if getline(l:i) =~# '!task\>\|!AR\>'
      return 1
    endif
  endfor
  return 0
endfunction

function! s:VegaNotesSniff() abort
  if &filetype ==# 'veganotes'
    return
  endif
  if s:IsVegaPath(expand('%:p')) || s:HasVegaMarkers()
    set filetype=veganotes
  endif
endfunction

function! s:VegaNotesPromote() abort
  if &filetype ==# 'veganotes'
    return
  endif
  if &filetype !=# 'markdown' && &filetype !=# ''
    return
  endif
  if s:IsVegaPath(expand('%:p')) || s:HasVegaMarkers()
    set filetype=veganotes
  endif
endfunction


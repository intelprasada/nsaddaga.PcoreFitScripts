" Auto-detect VegaNotes files.
"
" Anything under a `notes/` directory (e.g. .devdata/notes/, ~/notes/) or any
" .md file containing the !task / !AR markers becomes filetype=veganotes.
augroup veganotes_detect
  autocmd!
  autocmd BufRead,BufNewFile */notes/*.md,*/notes/**/*.md  set filetype=veganotes
  autocmd BufRead,BufNewFile *.veganotes,*.vnote           set filetype=veganotes
  " Heuristic: any .md with VegaNotes markers in first 50 lines overrides
  " the default markdown filetype.
  autocmd BufRead *.md call s:VegaNotesSniff()
augroup END

function! s:VegaNotesSniff() abort
  if &filetype ==# 'veganotes'
    return
  endif
  let l:n = min([50, line('$')])
  for l:i in range(1, l:n)
    if getline(l:i) =~# '!task\>\|!AR\>'
      set filetype=veganotes
      return
    endif
  endfor
endfunction

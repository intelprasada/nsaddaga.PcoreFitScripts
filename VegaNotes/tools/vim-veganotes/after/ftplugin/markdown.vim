" Runs after Vim's stock ftplugin/markdown.vim. If this markdown buffer is
" actually a VegaNotes note, switch the filetype now that the markdown
" filetype's settings have been applied (and would otherwise stick).
if &filetype !=# 'markdown'
  finish
endif

let s:path = expand('%:p')
let s:is_vega = s:path =~# '\v[\\/]notes[\\/]' || s:path =~# '\.\(veganotes\|vnote\)$'

if !s:is_vega
  let s:n = min([80, line('$')])
  for s:i in range(1, s:n)
    if getline(s:i) =~# '!task\>\|!AR\>'
      let s:is_vega = 1
      break
    endif
  endfor
endif

if s:is_vega
  set filetype=veganotes
endif

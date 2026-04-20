" VegaNotes ftplugin: editor ergonomics tuned for the markdown task notation.

if exists("b:did_ftplugin")
  finish
endif
let b:did_ftplugin = 1

setlocal expandtab
setlocal shiftwidth=2
setlocal softtabstop=2
setlocal tabstop=2
setlocal autoindent
setlocal linebreak
setlocal wrap
setlocal conceallevel=0
setlocal commentstring=<!--\ %s\ -->
setlocal formatoptions+=ron
setlocal iskeyword+=-,@-@,#-#,!,.

" Helpful jumps:
"   ]t / [t  : next/prev !task line
"   ]a / [a  : next/prev !AR line
nnoremap <silent><buffer> ]t :call search('!task\>', 'W')<CR>
nnoremap <silent><buffer> [t :call search('!task\>', 'bW')<CR>
nnoremap <silent><buffer> ]a :call search('!AR\>',  'W')<CR>
nnoremap <silent><buffer> [a :call search('!AR\>',  'bW')<CR>

let b:undo_ftplugin = 'setlocal expandtab< shiftwidth< softtabstop< tabstop<'
      \ . ' autoindent< linebreak< wrap< conceallevel< commentstring<'
      \ . ' formatoptions< iskeyword<'
      \ . ' | nunmap <buffer> ]t | nunmap <buffer> [t'
      \ . ' | nunmap <buffer> ]a | nunmap <buffer> [a'

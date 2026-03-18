#!/bin/sh
cd /home/minato/fducsr/yian-generic/yian-lang/cmd/compiler/frontend/grammar
tree-sitter generate
tree-sitter build
# make
cp parser.so ../yian_lang_linux.so
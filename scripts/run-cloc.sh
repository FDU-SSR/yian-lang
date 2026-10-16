#!/bin/sh

#cd ..
cloc --read-lang-def=./scripts/cloc.config --include-lang=Python,YIAN,C python bench lib lsp runtime tests

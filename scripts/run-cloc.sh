#!/bin/sh

#cd ..
cloc --read-lang-def=./scripts/cloc.config --include-lang=Python,YIAN,C anx python bootstrap bench lib lsp runtime tests yianc

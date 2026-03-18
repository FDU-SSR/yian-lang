from dataclasses import dataclass
import os
from lian.config.config import LANG_SO_PATH

@dataclass
class LangConfig:
    name     : str
    parser   : object
    extension: list     = None
    so_path  : str      = LANG_SO_PATH


LANG_TABLE = []

LANG_EXTENSIONS = {}
EXTENSIONS_LANG = {}

def update_lang_extensions(lang_table, lang_list):
    global LANG_EXTENSIONS
    global EXTENSIONS_LANG

    for line in lang_table:
        LANG_EXTENSIONS[line.name] = line.extension

    # Adjust the attribution of .h files
    if "c" in lang_list:
        if ".h" in LANG_EXTENSIONS.get("cpp", []):
            LANG_EXTENSIONS["cpp"].remove(".h")
    elif "cpp" in lang_list:
        if ".h" in LANG_EXTENSIONS.get("c", []):
            LANG_EXTENSIONS["c"].remove(".h")

    for lang, exts in LANG_EXTENSIONS.items():
        for each_ext in exts:
            if each_ext not in EXTENSIONS_LANG:
                EXTENSIONS_LANG[each_ext] = lang
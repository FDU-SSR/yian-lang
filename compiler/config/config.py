import os
import platform
import sys

ROOT_DIR = os.path.realpath(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
LIAN_DIR = os.path.join(ROOT_DIR, "lian/src")
YIAN_DIR = os.path.join(ROOT_DIR)
sys.path.extend([LIAN_DIR, YIAN_DIR])


DEBUG_FLAG = False

YIAN_WORKSPACE_DIR = "yian_workspace"
DEFAULT_WORKSPACE_PATH = os.path.join(ROOT_DIR, "tests")
LANG_NAME = "yian"
LANG_EXTENSION = [".an"]

DEFAULT_SO_PATH = "yian_lang_linux.so"
if platform.system() == 'Darwin':
    if platform.machine() == 'arm64':
        DEFAULT_SO_PATH = "yian_lang_macos_arm64.so"
LANG_SO_PATH = os.path.join(YIAN_DIR, "compiler", "frontend", DEFAULT_SO_PATH)

BASIC_DIR = "basic"
INTERMEDIATE_RESULTS_DIR = "intermediate_results"
RESULTS_DIR = "results"
GENERICS_RESULTS = "generics"
OBJECTS_DIR = "objects"
BIN_DIR = "bin"
LOG_DIR = "log"
TYPE_SPACE_DIR = "type_space"
THIRD_PARTY_DIR = os.path.join(os.path.expanduser("~"), ".anx", "deps")


LRU_CACHE_CAPACITY = 10000
BUNDLE_CACHE_CAPACITY = 10

CUSTOM_MIN_TYPE_ID = 1000
DEFAULT_FAKE_UNIT_DATA_ID = 100
EXTERNAL_LIB_TYPE_ID = -100

UNIT_PUBLIC_SYMBOLS_PATH = "an.unit_public_symbols"
SYMBOL_SOURCE_IDS_PATH = "an.symbol_source_ids"
STMT_ID_TO_TYPE_ID_PATH = "an.stmt_type_id"
TYPE_DEFS_PATH = "an.stmt_type_defs"
UNIT_INFO_PATH = "an.unit_info"
SYMBOL_TYPE_DEFS_PATH = "an.symbol_type_defs"
STMT_TYPE_DEFS_PATH = "an.stmt_type_defs"
CALL_STMT_TYPE_PARAMETER_IDS_PATH = "an.call_type_parameter_ids"
CALL_STMT_ARGS_TO_TYPE_DEFS_PATH = "an.call_args_type_defs"

DEBUG_TYPE_SPACE_PATH = "an.debug_type_space"
TYPE_SPACE_PATH = "an.type_space"
HASH_TO_TYPE_ID_PATH = "an.hash_to_type_id"
METHOD_DECL_REFS_PATH = "an.method_decl_refs"
GLOBAL_TYPE_ID_PATH = "an.global_type_id"
API_DEMAND_MANAGER_PATH = "an.api_demand_manager"

LOCAL_LIB_ID = 0

LLVM_IR_OUTPUT_FILE_NAME = "output.ll"

cmake_minimum_required(VERSION 3.16)
project(benchmark LANGUAGES C)
option(BENCH_ASAN "Build benchmark with AddressSanitizer" OFF)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS ON)
add_executable(bench_app
  src/inspect.c
  src/main.c
  third_party/src/api.c
  third_party/src/dumper.c
  third_party/src/emitter.c
  third_party/src/loader.c
  third_party/src/parser.c
  third_party/src/reader.c
  third_party/src/scanner.c
  third_party/src/writer.c)
target_include_directories(bench_app PRIVATE third_party/include third_party/src)
target_compile_definitions(bench_app PRIVATE HAVE_CONFIG_H=1)
if(BENCH_ASAN)
  target_compile_options(bench_app PRIVATE -fsanitize=address -fno-omit-frame-pointer)
  target_link_options(bench_app PRIVATE -fsanitize=address)
endif()
if(UNIX)
  target_link_libraries(bench_app PRIVATE m)
endif()

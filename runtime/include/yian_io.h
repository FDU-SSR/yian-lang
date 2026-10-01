#ifndef YIAN_IO_H
#define YIAN_IO_H
#include <stdint.h>

/* 失败返回 -errno; 元数据字段为类型、长度、修改时间秒与纳秒。
 * 打开选项位为读、写、追加、创建、截断、排他创建。
 * 文件类型为普通文件=1、目录=2、符号链接=3、其他=4。 */
int32_t yian_io_error_kind(int32_t error);
int64_t yian_io_error_message(int32_t error, uint8_t *buffer, uint64_t capacity);
int64_t yian_io_open(const char *path, uint32_t options);
int64_t yian_io_read(int32_t fd, uint8_t *buffer, uint64_t length);
int64_t yian_io_write(int32_t fd, const uint8_t *buffer, uint64_t length);
int32_t yian_io_close(int32_t fd);
int64_t yian_io_seek(int32_t fd, int64_t offset, int32_t origin);
int32_t yian_io_set_len(int32_t fd, uint64_t length);
int32_t yian_io_sync(int32_t fd);
int32_t yian_io_metadata(const char *path, int32_t follow, uint64_t *fields);
int32_t yian_io_fmetadata(int32_t fd, uint64_t *fields);
int32_t yian_io_mkdir(const char *path);
int32_t yian_io_remove(const char *path, int32_t directory);
int32_t yian_io_rename(const char *from, const char *to);
int64_t yian_io_current_dir(uint8_t *buffer, uint64_t capacity);
int64_t yian_io_canonicalize(const char *path, uint8_t *buffer, uint64_t capacity);
void *yian_io_dir_open(const char *path, int32_t *error);
int64_t yian_io_dir_next(void *directory, uint8_t *buffer, uint64_t capacity);
int32_t yian_io_dir_close(void *directory);
#endif

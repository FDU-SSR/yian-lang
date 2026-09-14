#include <libgen.h>
#include <stdio.h>
#include <string.h>

#define _TINYDIR_PATH_MAX 4096
#define _TINYDIR_FILENAME_MAX 256

int tinydir_file_open(const char *path) {
  char dir_name_buf[_TINYDIR_PATH_MAX];
  char file_name_buf[_TINYDIR_FILENAME_MAX];
  char *dir_name;
  char *base_name;

  if (path == NULL || strlen(path) == 0 || strlen(path) >= _TINYDIR_PATH_MAX) {
    return -1;
  }

  /* Get the parent path */
  strcpy(dir_name_buf, path);
  dir_name = dirname(dir_name_buf);
  strcpy(file_name_buf, path); // Problem path length can be greater than 256 leading to stack buffer oveflow of file_name_buf buffer
  base_name = basename(file_name_buf);
  if ((strcmp(base_name, "/")) == 0) {
    printf("Folder path provided: %s\n", dir_name);
  } else {
    printf("File path provided: %s\n", base_name);
  }

  return 0;
}

int main(int argc, char *argv[]) {
  char* path = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
  if (tinydir_file_open(path) == -1) {
    printf("Error");
    return 1;
  }
}

// int tinydir_file_open(tinydir_file *file, const _tinydir_char_t *path)
// {
//   _tinydir_char_t dir_name_buf[_TINYDIR_PATH_MAX];
// 	_tinydir_char_t file_name_buf[_TINYDIR_FILENAME_MAX];
//   ...
// 	/* Concatenate the file name and extension to form base name */
// 	_tinydir_strcat(file_name_buf, ext_buf); // VULN: since sizeof(file_name_buf) + sizeof(ext_buf) is larger than sizeof(file_name_buf), we have a potential stack buffer overflow
// 	base_name = file_name_buf;

//   ...
// 	_tinydir_strcpy(file_name_buf, path); // VULN: since sizeof(file_name_buf) is smaller than the maximum path length, we have a potential stack buffer overflow
// 	base_name = basename(file_name_buf);

//   ...
// }
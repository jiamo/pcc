#include "_fake_defines.h"
#include "_fake_typedefs.h"

typedef struct __pcc_dirstream DIR;

struct dirent {
#if defined(__APPLE__)
    ino_t d_ino;
    off_t d_seekoff;
    unsigned short d_reclen;
    unsigned short d_namlen;
    unsigned char d_type;
    char d_name[1024];
#else
    ino_t d_ino;
    off_t d_off;
    unsigned short d_reclen;
    unsigned char d_type;
    char d_name[256];
#endif
};

DIR *opendir(const char *name);
DIR *fdopendir(int fd);
struct dirent *readdir(DIR *dirp);
int closedir(DIR *dirp);
void rewinddir(DIR *dirp);
int dirfd(DIR *dirp);

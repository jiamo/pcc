#ifndef _FAKE_DEFINES_H
#define _FAKE_DEFINES_H

#define NULL 0
#define EOF (-1)

#define BUFSIZ 1024
#define FOPEN_MAX 20
#define FILENAME_MAX 1024
#define L_tmpnam 1024
#define TMP_MAX 238328

#ifndef SEEK_SET
#define SEEK_SET 0
#endif
#ifndef SEEK_CUR
#define SEEK_CUR 1
#endif
#ifndef SEEK_END
#define SEEK_END 2
#endif

#define __LITTLE_ENDIAN 1234
#define LITTLE_ENDIAN __LITTLE_ENDIAN
#define __BIG_ENDIAN 4321
#define BIG_ENDIAN __BIG_ENDIAN
#define __BYTE_ORDER __LITTLE_ENDIAN
#define BYTE_ORDER __BYTE_ORDER

#define CHAR_BIT 8

#define EXIT_FAILURE 1
#define EXIT_SUCCESS 0

#define SCHAR_MAX 127
#define UCHAR_MAX 255
#define SHRT_MAX 32767
#define USHRT_MAX 65535
#define INT_MAX 2147483647
#define INT_MIN (-INT_MAX - 1)
#define UINT_MAX 4294967295U
#define LONG_MAX 9223372036854775807L
#define LONG_MIN (-LONG_MAX - 1L)
#define ULONG_MAX 18446744073709551615UL
#define LLONG_MAX 9223372036854775807LL
#define LLONG_MIN (-LLONG_MAX - 1LL)
#define ULLONG_MAX 18446744073709551615ULL

#define RAND_MAX 2147483647

/* C99 stdbool.h defines */
#define __bool_true_false_are_defined 1
#define false 0
#define true 1

/* va_arg macros and type */
#define va_start(_ap, _type) __builtin_va_start(&(_ap))
#define va_arg(_ap, _type) (*((_type *)__builtin_va_arg(&(_ap), sizeof(_type))))
#define va_end(_ap) __builtin_va_end(&(_ap))
#define va_copy(_dst, _src) __builtin_va_copy(&(_dst), &(_src))

#endif

#ifndef PCC_FAKE_STRUCTMEMBER_H
#define PCC_FAKE_STRUCTMEMBER_H

/* CPython structmember.h surface: PyMemberDef type codes + flags. PyMemberDef
 * itself is declared in <Python.h>. Values match CPython so extension member
 * tables (e.g. numpy) parse and the type codes compare correctly. */
#include <Python.h>

/* member type codes (T_*) */
#define T_SHORT 0
#define T_INT 1
#define T_LONG 2
#define T_FLOAT 3
#define T_DOUBLE 4
#define T_STRING 5
#define T_OBJECT 6
#define T_CHAR 7
#define T_BYTE 8
#define T_UBYTE 9
#define T_USHORT 10
#define T_UINT 11
#define T_ULONG 12
#define T_STRING_INPLACE 13
#define T_BOOL 14
#define T_OBJECT_EX 16
#define T_LONGLONG 17
#define T_ULONGLONG 18
#define T_PYSSIZET 19
#define T_NONE 20

/* member flags */
#define READONLY 1
#define READ_RESTRICTED 2
#define PY_AUDIT_READ 2
#define PY_WRITE_RESTRICTED 4
#define RESTRICTED (READ_RESTRICTED | PY_WRITE_RESTRICTED)

#endif /* PCC_FAKE_STRUCTMEMBER_H */

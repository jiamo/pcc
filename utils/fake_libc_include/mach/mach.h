#ifndef _FAKE_MACH_MACH_H
#define _FAKE_MACH_MACH_H

#include "../_fake_defines.h"
#include "../_fake_typedefs.h"

/* Minimal Mach task-info surface for pcc-compiled runtime sources
 * (py_os_rss.c). Layouts and constants are locked against the macOS SDK
 * by tests/python/test_sdk_struct_helpers_pcc.py:
 *   sizeof(struct mach_task_basic_info) == 48
 *   offsetof(resident_size) == 8
 *   MACH_TASK_BASIC_INFO_COUNT == 12, MACH_TASK_BASIC_INFO == 20
 *   KERN_SUCCESS == 0
 */

typedef int kern_return_t;
typedef unsigned int natural_t;
typedef int integer_t;
typedef unsigned int mach_msg_type_number_t;
typedef unsigned int mach_port_t;
typedef mach_port_t task_name_t;
typedef natural_t task_flavor_t;
typedef integer_t *task_info_t;
typedef unsigned long long mach_vm_size_t;
typedef int policy_t;

typedef struct time_value {
    integer_t seconds;
    integer_t microseconds;
} time_value_t;

struct mach_task_basic_info {
    mach_vm_size_t virtual_size;
    mach_vm_size_t resident_size;
    mach_vm_size_t resident_size_max;
    time_value_t user_time;
    time_value_t system_time;
    policy_t policy;
    integer_t suspend_count;
};
typedef struct mach_task_basic_info *mach_task_basic_info_t;

#define MACH_TASK_BASIC_INFO 20
#define MACH_TASK_BASIC_INFO_COUNT 12
#define KERN_SUCCESS 0

extern mach_port_t mach_task_self_;
#define mach_task_self() mach_task_self_

kern_return_t task_info(
    task_name_t target_task,
    task_flavor_t flavor,
    task_info_t task_info_out,
    mach_msg_type_number_t *task_info_outCnt
);

#endif

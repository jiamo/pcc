#define _POSIX_C_SOURCE 200809L

#include "py_runtime.h"

#include <errno.h>
#include <inttypes.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>

#if defined(__APPLE__)
#include <mach/mach.h>
#elif defined(__linux__)
#include <sys/types.h>
#endif

static int64_t now_ns(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return -1;
    return (int64_t)ts.tv_sec * INT64_C(1000000000) + (int64_t)ts.tv_nsec;
}

static int64_t current_rss_bytes(void) {
#if defined(__APPLE__)
    mach_task_basic_info_data_t info;
    mach_msg_type_number_t count = MACH_TASK_BASIC_INFO_COUNT;
    kern_return_t rc = task_info(
        mach_task_self(), MACH_TASK_BASIC_INFO,
        (task_info_t)&info, &count
    );
    return rc == KERN_SUCCESS ? (int64_t)info.resident_size : -1;
#elif defined(__linux__)
    FILE *fp = fopen("/proc/self/statm", "r");
    if (fp == NULL) return -1;
    unsigned long total_pages = 0;
    unsigned long resident_pages = 0;
    int fields = fscanf(fp, "%lu %lu", &total_pages, &resident_pages);
    fclose(fp);
    (void)total_pages;
    long page_size = sysconf(_SC_PAGESIZE);
    if (fields != 2 || page_size <= 0) return -1;
    return (int64_t)resident_pages * (int64_t)page_size;
#else
    return -1;
#endif
}

static int64_t peak_rss_bytes(void) {
#if defined(__APPLE__)
    task_vm_info_data_t info;
    mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
    kern_return_t rc = task_info(
        mach_task_self(), TASK_VM_INFO,
        (task_info_t)&info, &count
    );
    return rc == KERN_SUCCESS ? (int64_t)info.resident_size_peak : -1;
#else
    struct rusage usage;
    if (getrusage(RUSAGE_SELF, &usage) != 0) return -1;
    return (int64_t)usage.ru_maxrss * INT64_C(1024);
#endif
}

static int fail(int code, const char *phase, int64_t detail) {
    fprintf(
        stderr,
        "vthread-real-runtime failure code=%d phase=%s detail=%" PRId64 "\n",
        code, phase, detail
    );
    return code;
}

static void progress(
    int64_t backend,
    const char *phase,
    int64_t done,
    int64_t total
) {
    fprintf(
        stderr,
        "vthread-real-runtime backend=%" PRId64
        " phase=%s done=%" PRId64 "/%" PRId64 "\n",
        backend, phase, done, total
    );
    fflush(stderr);
}

static int64_t safe_mean(int64_t total, int64_t count) {
    return count > 0 ? total / count : 0;
}

int main(int argc, char **argv) {
    if (argc != 5) {
        fprintf(stderr, "usage: %s BACKEND N TIMER_N IO_N\n", argv[0]);
        return 2;
    }
    int64_t backend = strtoll(argv[1], NULL, 10);
    int64_t n = strtoll(argv[2], NULL, 10);
    int64_t timer_n = strtoll(argv[3], NULL, 10);
    int64_t io_n = strtoll(argv[4], NULL, 10);
    if (
        backend < 0 || backend > 4 || n <= 0 || timer_n < 0 || io_n < 0
        || timer_n + io_n >= n
    ) {
        return fail(3, "arguments", n);
    }
    if (setenv("PCC_VTHREAD_IO_BACKEND", "poll", 1) != 0) {
        return fail(4, "setenv", errno);
    }
    if (pcc_gc_set_backend(backend) != 0) {
        return fail(5, "gc-backend", backend);
    }
    pcc_gc_telemetry_reset();
    (void)py_virtual_thread_effect_reset();

    int fds[2];
    if (pipe(fds) != 0) return fail(6, "pipe", errno);

    int64_t ready_n = n - timer_n - io_n;
    int64_t create_ns = 0;
    int64_t enqueue_ns = 0;
    int64_t timer_park_ns = 0;
    int64_t io_park_ns = 0;
    int64_t timer_wake_ns = 0;
    int64_t io_wake_ns = 0;
    int64_t resume_ns = 0;
    int64_t complete_ns = 0;
    int64_t live_collect_wall_ns = 0;
    int64_t final_collect_wall_ns = 0;
    int64_t total_start = now_ns();
    int64_t progress_step = n >= 10 ? n / 10 : n;
    progress(backend, "schedule", 0, n);

    for (int64_t i = 0; i < n; i++) {
        int64_t t0 = now_ns();
        PyObject *vt = py_virtual_thread_new(py_None);
        int64_t t1 = now_ns();
        if (vt == NULL || t0 < 0 || t1 < t0) {
            return fail(10, "create", i);
        }
        create_ns += t1 - t0;
        int64_t rc = 0;
        int64_t t2 = 0;
        if (i < io_n) {
            rc = py_virtual_thread_block_on_fd(vt, fds[0], POLLIN, -1);
            t2 = now_ns();
            io_park_ns += t2 - t1;
            if (rc != 0) return fail(11, "io-park", i);
        } else if (i < io_n + timer_n) {
            rc = py_virtual_thread_sleep(vt, 1);
            t2 = now_ns();
            timer_park_ns += t2 - t1;
            if (rc != 0) return fail(12, "timer-park", i);
        } else {
            rc = py_virtual_thread_start(vt);
            t2 = now_ns();
            enqueue_ns += t2 - t1;
            if (rc != 0) return fail(13, "enqueue", i);
        }
        pcc_gc_release(vt);
        if (
            progress_step > 0
            && ((i + 1) % progress_step == 0 || i + 1 == n)
        ) {
            progress(backend, "schedule", i + 1, n);
        }
    }

    int64_t ready_before = py_virtual_thread_ready_count();
    int64_t timers_before = py_virtual_thread_timer_count();
    int64_t io_before = py_virtual_thread_io_wait_count();
    int64_t roots_before = pcc_gc_scheduler_root_count();
    if (ready_before != ready_n) return fail(14, "ready-before", ready_before);
    if (timers_before != timer_n) return fail(15, "timers-before", timers_before);
    if (io_before != io_n) return fail(16, "io-before", io_before);
    if (roots_before != n) return fail(17, "roots-before", roots_before);
    int64_t rss_live = current_rss_bytes();

    int64_t t0 = now_ns();
    (void)pcc_gc_collect(0);
    int64_t t1 = now_ns();
    if (t0 < 0 || t1 < t0) return fail(18, "live-collect-clock", 0);
    live_collect_wall_ns = t1 - t0;
    if (pcc_gc_scheduler_root_count() != n) {
        return fail(19, "roots-after-live-collect", pcc_gc_scheduler_root_count());
    }

    if (timer_n > 0) {
        struct timespec delay = {.tv_sec = 0, .tv_nsec = 2000000};
        (void)nanosleep(&delay, NULL);
        t0 = now_ns();
        int64_t timer_woken = py_virtual_thread_poll_timers();
        t1 = now_ns();
        timer_wake_ns = t1 - t0;
        if (timer_woken != timer_n) return fail(20, "timer-wake", timer_woken);
    }
    if (io_n > 0) {
        char byte = 'r';
        if (write(fds[1], &byte, 1) != 1) return fail(21, "io-write", errno);
        t0 = now_ns();
        int64_t io_woken = py_virtual_thread_poll_io(0);
        t1 = now_ns();
        io_wake_ns = t1 - t0;
        if (io_woken != io_n) return fail(22, "io-wake", io_woken);
    }
    if (py_virtual_thread_ready_count() != n) {
        return fail(23, "ready-after-wake", py_virtual_thread_ready_count());
    }
    if (py_virtual_thread_timer_count() != 0) {
        return fail(24, "timers-after-wake", py_virtual_thread_timer_count());
    }
    if (py_virtual_thread_io_wait_count() != 0) {
        return fail(25, "io-after-wake", py_virtual_thread_io_wait_count());
    }

    progress(backend, "complete", 0, n);
    for (int64_t i = 0; i < n; i++) {
        t0 = now_ns();
        PyObject *vt = py_virtual_thread_poll_ready();
        t1 = now_ns();
        if (vt == NULL) return fail(30, "resume", i);
        resume_ns += t1 - t0;
        if (py_virtual_thread_complete(vt, py_None) != 0) {
            return fail(31, "complete", i);
        }
        int64_t t2 = now_ns();
        complete_ns += t2 - t1;
        pcc_gc_release(vt);
        if (
            progress_step > 0
            && ((i + 1) % progress_step == 0 || i + 1 == n)
        ) {
            progress(backend, "complete", i + 1, n);
        }
    }

    if (py_virtual_thread_ready_count() != 0) {
        return fail(32, "ready-final", py_virtual_thread_ready_count());
    }
    if (pcc_gc_scheduler_root_count() != 0) {
        return fail(33, "roots-final", pcc_gc_scheduler_root_count());
    }
    t0 = now_ns();
    (void)pcc_gc_collect(0);
    t1 = now_ns();
    final_collect_wall_ns = t1 - t0;
    int64_t total_ns = t1 - total_start;
    int64_t rss_final = current_rss_bytes();
    int64_t rss_peak = peak_rss_bytes();
    close(fds[0]);
    close(fds[1]);

    int64_t throughput = total_ns > 0
        ? (int64_t)((long double)n * 1000000000.0L / (long double)total_ns)
        : 0;
    printf(
        "{"
        "\"backend\":%" PRId64 ","
        "\"backend_name\":\"%s\","
        "\"n\":%" PRId64 ","
        "\"completed\":%" PRId64 ","
        "\"ready_n\":%" PRId64 ","
        "\"timer_n\":%" PRId64 ","
        "\"io_n\":%" PRId64 ","
        "\"rss_live_bytes\":%" PRId64 ","
        "\"rss_final_bytes\":%" PRId64 ","
        "\"peak_rss_bytes\":%" PRId64 ","
        "\"total_ns\":%" PRId64 ","
        "\"throughput_vthreads_per_sec\":%" PRId64 ","
        "\"create_mean_ns\":%" PRId64 ","
        "\"enqueue_mean_ns\":%" PRId64 ","
        "\"timer_park_mean_ns\":%" PRId64 ","
        "\"timer_wake_mean_ns\":%" PRId64 ","
        "\"io_park_mean_ns\":%" PRId64 ","
        "\"io_wake_mean_ns\":%" PRId64 ","
        "\"resume_mean_ns\":%" PRId64 ","
        "\"complete_mean_ns\":%" PRId64 ","
        "\"live_collect_wall_ns\":%" PRId64 ","
        "\"final_collect_wall_ns\":%" PRId64 ","
        "\"gc_pause_count\":%" PRId64 ","
        "\"gc_pause_sum_us\":%" PRId64 ","
        "\"gc_pause_max_us\":%" PRId64 ","
        "\"gc_pause_hist_lt_100us\":%" PRId64 ","
        "\"gc_pause_hist_lt_1ms\":%" PRId64 ","
        "\"gc_pause_hist_lt_10ms\":%" PRId64 ","
        "\"gc_pause_hist_ge_10ms\":%" PRId64 ","
        "\"effect_events_recorded\":%" PRId64 ","
        "\"effect_events_dropped\":%" PRId64 ","
        "\"scheduler_roots_final\":%" PRId64 ","
        "\"ready_final\":%" PRId64 ","
        "\"timer_final\":%" PRId64 ","
        "\"io_final\":%" PRId64
        "}\n",
        backend, pcc_gc_backend_name(backend), n, n, ready_n, timer_n, io_n,
        rss_live, rss_final, rss_peak, total_ns, throughput,
        safe_mean(create_ns, n), safe_mean(enqueue_ns, ready_n),
        safe_mean(timer_park_ns, timer_n), safe_mean(timer_wake_ns, timer_n),
        safe_mean(io_park_ns, io_n), safe_mean(io_wake_ns, io_n),
        safe_mean(resume_ns, n), safe_mean(complete_ns, n),
        live_collect_wall_ns, final_collect_wall_ns,
        pcc_gc_telemetry(PCC_GC_COUNTER_PAUSE_COUNT),
        pcc_gc_telemetry(PCC_GC_COUNTER_PAUSE_SUM_US),
        pcc_gc_telemetry(PCC_GC_COUNTER_MAX_PAUSE_US),
        pcc_gc_telemetry(PCC_GC_COUNTER_PAUSE_HIST_LT_100US),
        pcc_gc_telemetry(PCC_GC_COUNTER_PAUSE_HIST_LT_1MS),
        pcc_gc_telemetry(PCC_GC_COUNTER_PAUSE_HIST_LT_10MS),
        pcc_gc_telemetry(PCC_GC_COUNTER_PAUSE_HIST_GE_10MS),
        py_virtual_thread_effect_count(), py_virtual_thread_effect_dropped(),
        pcc_gc_scheduler_root_count(), py_virtual_thread_ready_count(),
        py_virtual_thread_timer_count(), py_virtual_thread_io_wait_count()
    );
    return 0;
}

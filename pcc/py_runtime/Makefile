# pcc/py_runtime/Makefile
#
# Build libpy_runtime.a, the C runtime for pcc's Python frontend.
#
# Public header:  include/py_runtime.h
# Source:         src/*.c  +  py/*.py  (Phase 4c runtime-high migrations)
# Output:         libpy_runtime.a (in this directory)
#
# Usage:
#   make                          - build libpy_runtime.a (cc baseline)
#   make libpy_runtime_pcc.a      - build pcc-emitted runtime archive (Phase 1/2)
#   make libpy_runtime_pcc_py.a   - build archive with pcc-Python runtime-high (Phase 4c)
#   make clean                    - remove all objects + archives
#   make distclean                - clean + drop pcc-emitted trees too

CC       ?= cc
AR       ?= ar
RANLIB   ?= ranlib
PCC      ?= pcc
CLANG    ?= clang
PYTHON   ?= python3
PCC_IR_TO_OBJ ?= $(PYTHON) -m pcc.tools.ir_to_obj
PCC_REPO_ROOT ?= $(abspath ../..)

CFLAGS   ?= -O2 -fPIC -Wall -Wextra -std=c11
CPPFLAGS ?=
INCLUDES  = -Iinclude
PCC_WITH_THREADS ?= 0
PCC_REFCOUNT_KIND ?=
RUNTIME_HEADERS = Makefile include/py_runtime.h $(SRCDIR)/py_internal.h $(SRCDIR)/py_timer_heap.h $(SRCDIR)/py_io_waitset.h
PCC_STDATOMIC_HEADER = $(PCC_REPO_ROOT)/utils/fake_libc_include/stdatomic.h

# Phase 4: optional libpython fallback. Build with
#   make PCC_WITH_LIBPYTHON=1
# to enable the CPython C-API path in py_libpython.c. Off by default
# so the default runtime has no libpython dependency.
# Linux/glibc: -std=c11 defines __STRICT_ANSI__, which switches off glibc's
# default feature set and hides POSIX/BSD declarations (CLOCK_MONOTONIC,
# struct addrinfo, struct stat::st_mtim, ...). Restore the default set in ONE
# place instead of sprinkling feature macros per file; _DEFAULT_SOURCE does not
# enable any GNU-only compiler extension, so the strict C11 dialect is kept.
# Darwin's headers declare these unconditionally and need nothing.
ifneq ($(shell uname),Darwin)
CPPFLAGS += -D_DEFAULT_SOURCE
endif

ifeq ($(PCC_WITH_THREADS),1)
CPPFLAGS += -DPCC_WITH_THREADS=1
CFLAGS   += -pthread
else
CPPFLAGS += -DPCC_WITH_THREADS=0
endif

ifneq ($(PCC_REFCOUNT_KIND),)
CPPFLAGS += -DPCC_REFCOUNT_STRATEGY=$(PCC_REFCOUNT_KIND)
endif

ifeq ($(PCC_WITH_LIBPYTHON),1)
CFLAGS   += -DPCC_WITH_LIBPYTHON=1 $(shell python3-config --includes)
endif

SRCDIR       = src
PYDIR        = py
OBJDIR       = build
OBJDIR_PCC   = build_pcc
OBJDIR_PY    = build_py
OBJDIR_LIBPY = build_libpython

# Runtime modules that exist as Python ports under py/ and are used by
# the libpy_runtime_pcc_py.a variant in place of the cc-built .o.
# Keep in sync with the list of @c_abi_export-decorated modules in py/.
PY_MODULES = py_tuple py_tuple_slice py_obj_stubs py_exc_tls py_exc_objects py_exc_match py_exc_table py_exc_traceback py_obj_gc py_gc_telemetry py_gc_backend py_set py_os_env py_os_path py_os_substrate py_process py_process_substrate py_print_sys py_print_fmt py_obj py_obj_dealloc py_dict py_list py_list_set_slice py_iter py_gen py_coroutine py_func py_re py_dunder py_weakref py_file py_threading py_class py_obj_ops_dispatch py_obj_ops_slice py_obj_ops_set_slice py_obj_ops_mod py_obj_ops_compare py_str py_str_accessors py_str_slice py_int_core py_int_decimal py_int_shift py_int_bitwise py_int_addsub py_int_mul py_int_bigint_pow py_int_ops py_int py_int_parse py_int_convert py_int_bigint_convert py_substrate

# C objects that are replaced indirectly by a Python port with a different
# module name. py_obj_stubs.py owns the exported bytes helpers in the
# pcc-Python runtime archive, so the C py_bytes.o must not remain there.
PY_REPLACED_C_MODULES = $(PY_MODULES) py_bytes

SRCS = \
	$(SRCDIR)/py_obj.c \
	$(SRCDIR)/py_dunder.c \
	$(SRCDIR)/py_obj_dealloc.c \
	$(SRCDIR)/py_gc_index_table.c \
	$(SRCDIR)/py_os_native.c \
	$(SRCDIR)/py_gc_backend.c \
	$(SRCDIR)/pcc_threads.c \
	$(SRCDIR)/py_timer_heap.c \
	$(SRCDIR)/py_io_waitset.c \
	$(SRCDIR)/pcc_runtime_log.c \
	$(SRCDIR)/pcc_metal_runtime.c \
	$(SRCDIR)/pcc_gc_external_resource.c \
	$(SRCDIR)/pcc_dlpack_runtime.c \
	$(SRCDIR)/py_obj_gc.c \
	$(SRCDIR)/py_protocol.c \
	$(SRCDIR)/py_obj_ops_compare.c \
	$(SRCDIR)/py_obj_ops_dispatch.c \
	$(SRCDIR)/py_func.c \
	$(SRCDIR)/py_int_core.c \
	$(SRCDIR)/py_int_decimal.c \
	$(SRCDIR)/py_int_shift.c \
	$(SRCDIR)/py_int_bitwise.c \
	$(SRCDIR)/py_int_addsub.c \
	$(SRCDIR)/py_int_mul.c \
	$(SRCDIR)/py_int_bigint_pow.c \
	$(SRCDIR)/py_int_ops.c \
	$(SRCDIR)/py_int.c \
	$(SRCDIR)/py_int_parse.c \
	$(SRCDIR)/py_int_convert.c \
	$(SRCDIR)/py_int_bigint_convert.c \
	$(SRCDIR)/py_str.c \
	$(SRCDIR)/py_str_accessors.c \
	$(SRCDIR)/py_json.c \
	$(SRCDIR)/py_pickle_copy.c \
	$(SRCDIR)/py_bytes.c \
	$(SRCDIR)/py_list.c \
	$(SRCDIR)/py_iter.c \
	$(SRCDIR)/py_gen.c \
	$(SRCDIR)/py_coroutine.c \
	$(SRCDIR)/py_asyncio_io.c \
	$(SRCDIR)/py_context.c \
		$(SRCDIR)/py_format.c \
		$(SRCDIR)/py_float_fromhex.c \
		$(SRCDIR)/py_complex_pow.c \
		$(SRCDIR)/py_call_splat.c \
		$(SRCDIR)/py_module_attrs.c \
		$(SRCDIR)/py_compiled_module.c \
		$(PY_CAPI_SHIM_SRC) \
		$(SRCDIR)/py_extension_loader.c \
		$(SRCDIR)/py_tuple.c \
	$(SRCDIR)/py_dict.c \
	$(SRCDIR)/py_set.c \
	$(SRCDIR)/py_class.c \
	$(SRCDIR)/py_class_attrs.c \
	$(SRCDIR)/py_exc_tls.c \
	$(SRCDIR)/py_exc_table.c \
	$(SRCDIR)/py_exc_objects.c \
	$(SRCDIR)/py_exc_match.c \
	$(SRCDIR)/py_exc_traceback.c \
	$(SRCDIR)/py_os_env.c \
	$(SRCDIR)/py_os_path.c \
	$(SRCDIR)/py_os_substrate.c \
	$(SRCDIR)/py_http.c \
	$(SRCDIR)/py_file.c \
	$(SRCDIR)/py_process.c \
	$(SRCDIR)/py_process_substrate.c \
	$(SRCDIR)/py_process_timeout.c \
	$(SRCDIR)/py_re.c \
	$(SRCDIR)/py_re_engine.c \
	$(SRCDIR)/py_re_engine_obj.c \
	$(SRCDIR)/py_cpy_handle.c \
	$(SRCDIR)/py_os_rss.c \
	$(SRCDIR)/py_os_heap.c \
	$(SRCDIR)/py_enumerate.c \
	$(SRCDIR)/py_int_bytes.c \
	$(SRCDIR)/py_weakref.c \
	$(SRCDIR)/py_threading.c \
	$(SRCDIR)/py_print_fmt.c \
	$(SRCDIR)/py_print_sys.c \
	$(SRCDIR)/py_obj_stubs.c \
	$(SRCDIR)/py_substrate.c \
	$(addprefix $(SRCDIR)/,$(LIBPYTHON_SRCS))

LIBPYTHON_SRCS =
ifeq ($(PCC_WITH_LIBPYTHON),1)
LIBPYTHON_SRCS = py_libpython.c
endif

PY_CAPI_SHIM_SRC =
ifneq ($(PCC_WITH_LIBPYTHON),1)
PY_CAPI_SHIM_SRC = $(SRCDIR)/py_capi_shim.c
endif

OBJS     = $(patsubst $(SRCDIR)/%.c,$(OBJDIR)/%.o,$(SRCS))
OBJS_PCC = $(patsubst $(SRCDIR)/%.c,$(OBJDIR_PCC)/%.o,$(SRCS))
OBJS_PY  = $(patsubst %,$(OBJDIR_PY)/%.o,$(PY_MODULES))

LIB        = libpy_runtime.a
LIB_PCC    = libpy_runtime_pcc.a
LIB_PCC_PY = libpy_runtime_pcc_py.a
LIB_PCC_PY_LIBPYTHON = libpy_runtime_pcc_py_libpython.a
OBJ_PY_CC_HELPERS = $(OBJDIR_PY)/py_gc_index_table.o $(OBJDIR_PY)/py_os_native.o $(OBJDIR_PY)/pcc_threads.o $(OBJDIR_PY)/pcc_runtime_log.o $(OBJDIR_PY)/py_json.o $(OBJDIR_PY)/py_pickle_copy.o $(OBJDIR_PY)/py_runtime_high_substrate.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/pcc_metal_runtime.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/pcc_gc_external_resource.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/pcc_dlpack_runtime.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_context.o $(OBJDIR_PY)/py_format.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_process_timeout.o
# float.fromhex hex-float parser (C-only, no port; see py_float_fromhex.c).
# Linked into the default pcc-Python port archive so float.fromhex works
# under --python-libpython=off.
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_float_fromhex.o
# Complex ``**`` (C-only, no port; see py_complex_pow.c / .py). Linked into the
# default pcc-Python port archive alongside the other complex helpers.
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_complex_pow.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_asyncio_io.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_protocol.o $(OBJDIR_PY)/py_class_attrs.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_call_splat.o $(OBJDIR_PY)/py_module_attrs.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_compiled_module.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_capi_shim.o $(OBJDIR_PY)/py_extension_loader.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_http.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_int_modexp.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_obj_min_max.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_tuple_methods.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_re_engine.o $(OBJDIR_PY)/py_re_engine_obj.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_cpy_handle.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_os_rss.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_os_heap.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_enumerate.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_int_bytes.o
# Dependency-free timer min-heap. pcc_threads.o (a C-only helper above) now
# calls pcc_timer_heap_* for the vthread sleep/poll-timers path, so the object
# must be in the pcc-Python runtime archive too, not only the C-runtime one.
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_timer_heap.o
# Dependency-free IO waitset (poll fallback + Darwin kqueue). py_asyncio_io.o
# (a C-only helper above) now calls pcc_io_waitset_kqueue_available() to report
# the platform readiness backend, so the object must be in the pcc-Python
# runtime archive too, not only the C-runtime one.
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_io_waitset.o

.PHONY: all clean distclean

all: $(LIB)

$(LIB): $(OBJS)
	rm -f $@.tmp
	$(AR) rcs $@.tmp $^
	$(RANLIB) $@.tmp
	mv -f $@.tmp $@

$(OBJDIR)/%.o: $(SRCDIR)/%.c $(RUNTIME_HEADERS) | $(OBJDIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR):
	mkdir -p $(OBJDIR)

# Phase 1/2 track: same source compiled by pcc itself. Produces a
# distinct archive so the two builds never collide. The pcc invocation
# uses --cpp-arg=-I for header lookup because pcc does not (yet)
# consume -I directly; --emit-obj writes the object file in place.
$(LIB_PCC): $(OBJS_PCC)
	rm -f $@.tmp
	$(AR) rcs $@.tmp $^
	$(RANLIB) $@.tmp
	mv -f $@.tmp $@

$(OBJDIR_PCC)/%.o: $(SRCDIR)/%.c $(RUNTIME_HEADERS) | $(OBJDIR_PCC)
	$(PCC) --cpp-arg=-I$(CURDIR)/include --cpp-arg=-I$(CURDIR)/src --emit-obj $@ $<

# py_re_engine.c uses C11 atomics through pcc's parser-facing fake header.
# Track that preprocessing input so an atomic-surface edit cannot leave a stale
# pcc-emitted object/archive behind.
$(OBJDIR_PCC)/py_re_engine.o: $(PCC_STDATOMIC_HEADER)

# C-kernel platform-syscall helpers (PCC_CC_ONLY_SRCS): these read OS-specific,
# ABI-sensitive structs through SDK headers that use modern syntax pcc's C
# frontend does not parse (e.g. macOS <malloc/malloc.h> / <mach/mach.h> use C23
# `enum : uint64_t` and bool bitfields; <sys/event.h>'s kevent has no fake-libc
# shadow). fake-libc shadows would have to duplicate the OS struct layout
# exactly, which is fragile; per the runtime layering doctrine these stay in the
# C kernel. Their symbols may be referenced by other pcc-emitted objects in the
# SAME archive (e.g. py_asyncio_io.o calls pcc_io_waitset_kqueue_available), and
# that still links because the CC-built object is archived alongside them.
# Explicit rules override the generic pcc pattern rule above.
PCC_CC_ONLY_SRCS = py_os_rss py_os_heap py_io_waitset pcc_metal_runtime pcc_gc_external_resource pcc_dlpack_runtime
$(OBJDIR_PCC)/pcc_metal_runtime.o: $(SRCDIR)/pcc_metal_runtime.c $(RUNTIME_HEADERS) | $(OBJDIR_PCC)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@
$(OBJDIR_PCC)/pcc_gc_external_resource.o: $(SRCDIR)/pcc_gc_external_resource.c $(RUNTIME_HEADERS) | $(OBJDIR_PCC)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@
$(OBJDIR_PCC)/pcc_dlpack_runtime.o: $(SRCDIR)/pcc_dlpack_runtime.c $(RUNTIME_HEADERS) | $(OBJDIR_PCC)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@
$(OBJDIR_PCC)/py_os_rss.o: $(SRCDIR)/py_os_rss.c $(RUNTIME_HEADERS) | $(OBJDIR_PCC)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@
$(OBJDIR_PCC)/py_io_waitset.o: $(SRCDIR)/py_io_waitset.c $(RUNTIME_HEADERS) | $(OBJDIR_PCC)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@
$(OBJDIR_PCC)/py_os_heap.o: $(SRCDIR)/py_os_heap.c $(RUNTIME_HEADERS) | $(OBJDIR_PCC)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PCC):
	mkdir -p $(OBJDIR_PCC)

# Phase 4c track: runtime-high modules ported from C to pcc-compilable
# Python under py/. pcc emits library-shaped LLVM IR for each module
# without synthesizing a program @main, then ir_to_obj lowers it to a
# relocatable object. The resulting archive is built directly from the
# Python-built objects: the no-C closure must not depend on successfully
# compiling the replaced C runtime sources on every target.
# Build into a temp archive and atomically rename into place. A plain
# `rm $@; ar rcs $@` leaves a window where $@ is absent/partial; a concurrent
# linker (e.g. another xdist worker's clang, which holds no build lock) then
# fails with "no such file: libpy_runtime_pcc_py.a". `mv` is an atomic rename,
# so a linker always opens the old-or-new complete archive, never a gap.
$(LIB_PCC_PY): $(OBJS_PY) $(OBJ_PY_CC_HELPERS)
	rm -f $@.tmp
	$(AR) rcs $@.tmp $(OBJS_PY) $(OBJ_PY_CC_HELPERS)
	$(RANLIB) $@.tmp
	mv -f $@.tmp $@

# Compatibility fallback archive for bootstrap builds that still emit
# py_cpy_* calls. It keeps the pcc-Python runtime replacement archive
# as the base and adds only the CPython bridge object.
$(LIB_PCC_PY_LIBPYTHON): $(LIB_PCC_PY) $(OBJDIR_LIBPY)/py_libpython.o
	cp $(LIB_PCC_PY) $@.tmp
	-$(AR) d $@.tmp py_libpython.o
	# In libpython mode the real libpython provides the CPython C-API. The
	# no-libpython C-API shim (py_capi_shim.o: ~286 Py* symbols using pcc's
	# object model with no libpython delegation) must NOT be in this variant:
	# dlopen'd extensions are built -undefined dynamic_lookup, so their Py*
	# calls resolve from the executable's globals; if the shim is present it
	# SHADOWS libpython's real C-API and mishandles real CPython objects,
	# breaking C-extension module init (e.g. import numpy / unicodedata ->
	# "execution of module ... failed without setting an exception"). Drop it
	# so libpython's C-API wins. The no-libpython archive (LIB_PCC_PY) keeps
	# the shim and is unchanged, so self-host bootstrap is unaffected. See
	# docs/investigations/python-cpython-compat-import-numpy-multiarray-init-fails.md
	-$(AR) d $@.tmp py_capi_shim.o
	$(AR) r $@.tmp $(OBJDIR_LIBPY)/py_libpython.o
	$(RANLIB) $@.tmp
	mv -f $@.tmp $@

$(OBJDIR_LIBPY)/py_libpython.o: $(SRCDIR)/py_libpython.c $(RUNTIME_HEADERS) | $(OBJDIR_LIBPY)
	$(CC) $(CPPFLAGS) $(CFLAGS) -DPCC_WITH_LIBPYTHON=1 $(shell python3-config --includes) $(INCLUDES) -c $< -o $@

$(OBJDIR_LIBPY):
	mkdir -p $(OBJDIR_LIBPY)

# Recompile a pcc-Python runtime module when that module changes. The archive
# target already tracks additions/removals through OBJS_PY/OBJ_PY_CC_HELPERS;
# making every object depend on the entire Makefile turned any helper-list edit
# into a full runtime rebuild. Compiler/target/config invalidation is owned by
# pipeline._ensure_runtime's explicit full-rebuild decision.
$(OBJDIR_PY)/%.o: $(PYDIR)/%.py | $(OBJDIR_PY)
	PYTHONPATH=$(PCC_REPO_ROOT) PCC_HOST_PYTHON=$(PYTHON) $(PCC) --python-library --emit-llvm=$(OBJDIR_PY)/$*.ll $<
	PYTHONPATH=$(PCC_REPO_ROOT) $(PCC_IR_TO_OBJ) $(OBJDIR_PY)/$*.ll $@

$(OBJDIR_PY):
	mkdir -p $(OBJDIR_PY)

$(OBJDIR_PY)/py_gc_index_table.o: $(SRCDIR)/py_gc_index_table.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_os_native.o: $(SRCDIR)/py_os_native.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_http.o: $(SRCDIR)/py_http.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_asyncio_io.o: $(SRCDIR)/py_asyncio_io.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_process_timeout.o: $(SRCDIR)/py_process_timeout.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_io_waitset.o: $(SRCDIR)/py_io_waitset.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_int_modexp.o: $(SRCDIR)/py_int_modexp.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_obj_min_max.o: $(SRCDIR)/py_obj_min_max.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_tuple_methods.o: $(SRCDIR)/py_tuple_methods.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_re_engine.o: $(SRCDIR)/py_re_engine.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_re_engine_obj.o: $(SRCDIR)/py_re_engine_obj.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_cpy_handle.o: $(SRCDIR)/py_cpy_handle.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_os_rss.o: $(SRCDIR)/py_os_rss.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_os_heap.o: $(SRCDIR)/py_os_heap.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_enumerate.o: $(SRCDIR)/py_enumerate.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_int_bytes.o: $(SRCDIR)/py_int_bytes.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_timer_heap.o: $(SRCDIR)/py_timer_heap.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/pcc_threads.o: $(SRCDIR)/pcc_threads.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/pcc_runtime_log.o: $(SRCDIR)/pcc_runtime_log.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/pcc_metal_runtime.o: $(SRCDIR)/pcc_metal_runtime.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/pcc_gc_external_resource.o: $(SRCDIR)/pcc_gc_external_resource.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/pcc_dlpack_runtime.o: $(SRCDIR)/pcc_dlpack_runtime.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_json.o: $(SRCDIR)/py_json.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_pickle_copy.o: $(SRCDIR)/py_pickle_copy.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_runtime_high_substrate.o: $(SRCDIR)/py_runtime_high_substrate.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_protocol.o: $(SRCDIR)/py_protocol.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_class_attrs.o: $(SRCDIR)/py_class_attrs.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_context.o: $(SRCDIR)/py_context.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_format.o: $(SRCDIR)/py_format.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

# Explicit rule: build the port-archive py_float_fromhex.o from the C source
# (a C-only OBJ_PY_CC_HELPERS helper).
$(OBJDIR_PY)/py_float_fromhex.o: $(SRCDIR)/py_float_fromhex.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

# Explicit rule: build the port-archive py_complex_pow.o from the C source (a
# C-only OBJ_PY_CC_HELPERS helper). This overrides the generic
# $(OBJDIR_PY)/%.o: $(PYDIR)/%.py pattern rule so the co-located reference
# py_complex_pow.py (which is NOT a linked port module) is never compiled.
$(OBJDIR_PY)/py_complex_pow.o: $(SRCDIR)/py_complex_pow.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_call_splat.o: $(SRCDIR)/py_call_splat.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_module_attrs.o: $(SRCDIR)/py_module_attrs.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_compiled_module.o: $(SRCDIR)/py_compiled_module.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_capi_shim.o: $(SRCDIR)/py_capi_shim.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_extension_loader.o: $(SRCDIR)/py_extension_loader.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

clean:
	rm -rf $(OBJDIR) $(LIB)

distclean: clean
	rm -rf $(OBJDIR_PCC) $(LIB_PCC) $(OBJDIR_PY) $(LIB_PCC_PY) \
		$(OBJDIR_LIBPY) $(LIB_PCC_PY_LIBPYTHON)

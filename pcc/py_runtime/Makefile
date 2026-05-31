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
RUNTIME_HEADERS = Makefile include/py_runtime.h $(SRCDIR)/py_internal.h

# Phase 4: optional libpython fallback. Build with
#   make PCC_WITH_LIBPYTHON=1
# to enable the CPython C-API path in py_libpython.c. Off by default
# so the default runtime has no libpython dependency.
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
	$(SRCDIR)/pcc_runtime_log.c \
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
	$(SRCDIR)/py_context.c \
		$(SRCDIR)/py_format.c \
		$(SRCDIR)/py_call_splat.c \
		$(SRCDIR)/py_module_attrs.c \
		$(SRCDIR)/py_capi_shim.c \
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
	$(SRCDIR)/py_re.c \
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

OBJS     = $(patsubst $(SRCDIR)/%.c,$(OBJDIR)/%.o,$(SRCS))
OBJS_PCC = $(patsubst $(SRCDIR)/%.c,$(OBJDIR_PCC)/%.o,$(SRCS))
OBJS_PY  = $(patsubst %,$(OBJDIR_PY)/%.o,$(PY_MODULES))

LIB        = libpy_runtime.a
LIB_PCC    = libpy_runtime_pcc.a
LIB_PCC_PY = libpy_runtime_pcc_py.a
LIB_PCC_PY_LIBPYTHON = libpy_runtime_pcc_py_libpython.a
OBJ_PY_CC_HELPERS = $(OBJDIR_PY)/py_gc_index_table.o $(OBJDIR_PY)/py_os_native.o $(OBJDIR_PY)/pcc_threads.o $(OBJDIR_PY)/pcc_runtime_log.o $(OBJDIR_PY)/py_json.o $(OBJDIR_PY)/py_pickle_copy.o $(OBJDIR_PY)/py_runtime_high_substrate.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_context.o $(OBJDIR_PY)/py_format.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_protocol.o $(OBJDIR_PY)/py_class_attrs.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_call_splat.o $(OBJDIR_PY)/py_module_attrs.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_capi_shim.o $(OBJDIR_PY)/py_extension_loader.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_http.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_int_modexp.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_obj_min_max.o
OBJ_PY_CC_HELPERS += $(OBJDIR_PY)/py_tuple_methods.o

.PHONY: all clean distclean

all: $(LIB)

$(LIB): $(OBJS)
	rm -f $@
	$(AR) rcs $@ $^
	$(RANLIB) $@

$(OBJDIR)/%.o: $(SRCDIR)/%.c $(RUNTIME_HEADERS) | $(OBJDIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR):
	mkdir -p $(OBJDIR)

# Phase 1/2 track: same source compiled by pcc itself. Produces a
# distinct archive so the two builds never collide. The pcc invocation
# uses --cpp-arg=-I for header lookup because pcc does not (yet)
# consume -I directly; --emit-obj writes the object file in place.
$(LIB_PCC): $(OBJS_PCC)
	rm -f $@
	$(AR) rcs $@ $^
	$(RANLIB) $@

$(OBJDIR_PCC)/%.o: $(SRCDIR)/%.c $(RUNTIME_HEADERS) | $(OBJDIR_PCC)
	$(PCC) --cpp-arg=-I$(CURDIR)/include --cpp-arg=-I$(CURDIR)/src --emit-obj $@ $<

$(OBJDIR_PCC):
	mkdir -p $(OBJDIR_PCC)

# Phase 4c track: runtime-high modules ported from C to pcc-compilable
# Python under py/. pcc emits library-shaped LLVM IR for each module
# without synthesizing a program @main, then ir_to_obj lowers it to a
# relocatable object. The resulting archive is built directly from the
# Python-built objects: the no-C closure must not depend on successfully
# compiling the replaced C runtime sources on every target.
$(LIB_PCC_PY): $(OBJS_PY) $(OBJ_PY_CC_HELPERS)
	rm -f $@
	$(AR) rcs $@ $(OBJS_PY) $(OBJ_PY_CC_HELPERS)
	$(RANLIB) $@

# Compatibility fallback archive for bootstrap builds that still emit
# py_cpy_* calls. It keeps the pcc-Python runtime replacement archive
# as the base and adds only the CPython bridge object.
$(LIB_PCC_PY_LIBPYTHON): $(LIB_PCC_PY) $(OBJDIR_LIBPY)/py_libpython.o
	cp $(LIB_PCC_PY) $@
	-$(AR) d $@ py_libpython.o
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
	-$(AR) d $@ py_capi_shim.o
	$(AR) r $@ $(OBJDIR_LIBPY)/py_libpython.o
	$(RANLIB) $@

$(OBJDIR_LIBPY)/py_libpython.o: $(SRCDIR)/py_libpython.c $(RUNTIME_HEADERS) | $(OBJDIR_LIBPY)
	$(CC) $(CPPFLAGS) $(CFLAGS) -DPCC_WITH_LIBPYTHON=1 $(shell python3-config --includes) $(INCLUDES) -c $< -o $@

$(OBJDIR_LIBPY):
	mkdir -p $(OBJDIR_LIBPY)

$(OBJDIR_PY)/%.o: $(PYDIR)/%.py Makefile | $(OBJDIR_PY)
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

$(OBJDIR_PY)/py_int_modexp.o: $(SRCDIR)/py_int_modexp.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_obj_min_max.o: $(SRCDIR)/py_obj_min_max.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_tuple_methods.o: $(SRCDIR)/py_tuple_methods.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/pcc_threads.o: $(SRCDIR)/pcc_threads.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/pcc_runtime_log.o: $(SRCDIR)/pcc_runtime_log.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
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

$(OBJDIR_PY)/py_call_splat.o: $(SRCDIR)/py_call_splat.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR_PY)/py_module_attrs.o: $(SRCDIR)/py_module_attrs.c $(RUNTIME_HEADERS) | $(OBJDIR_PY)
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

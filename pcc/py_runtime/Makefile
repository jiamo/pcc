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

CFLAGS   ?= -O2 -fPIC -Wall -Wextra -std=c11
CPPFLAGS ?=
INCLUDES  = -Iinclude

# Phase 4: optional libpython fallback. Build with
#   make PCC_WITH_LIBPYTHON=1
# to enable the CPython C-API path in py_libpython.c. Off by default
# so the default runtime has no libpython dependency.
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
PY_MODULES = py_tuple py_obj_stubs py_exc_tls py_exc_objects py_exc_match py_exc_table py_exc_traceback py_obj_gc py_set py_os_env py_os_path py_process py_print_sys py_print_fmt py_obj py_obj_dealloc py_dict py_list py_class py_obj_ops_dispatch py_obj_ops_compare py_str py_str_accessors py_int_core py_int_decimal py_int_shift py_int_bitwise py_int_addsub py_int_mul py_int_bigint_pow py_int_ops py_int py_int_parse py_int_convert py_int_bigint_convert py_substrate

SRCS = \
	$(SRCDIR)/py_obj.c \
	$(SRCDIR)/py_dunder.c \
	$(SRCDIR)/py_obj_dealloc.c \
	$(SRCDIR)/py_obj_gc.c \
	$(SRCDIR)/py_obj_ops_compare.c \
	$(SRCDIR)/py_obj_ops_dispatch.c \
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
	$(SRCDIR)/py_list.c \
	$(SRCDIR)/py_tuple.c \
	$(SRCDIR)/py_dict.c \
	$(SRCDIR)/py_set.c \
	$(SRCDIR)/py_class.c \
	$(SRCDIR)/py_exc_tls.c \
	$(SRCDIR)/py_exc_table.c \
	$(SRCDIR)/py_exc_objects.c \
	$(SRCDIR)/py_exc_match.c \
	$(SRCDIR)/py_exc_traceback.c \
	$(SRCDIR)/py_os_env.c \
	$(SRCDIR)/py_os_path.c \
	$(SRCDIR)/py_os_substrate.c \
	$(SRCDIR)/py_file.c \
	$(SRCDIR)/py_process.c \
	$(SRCDIR)/py_process_substrate.c \
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

.PHONY: all clean distclean

all: $(LIB)

$(LIB): $(OBJS)
	rm -f $@
	$(AR) rcs $@ $^
	$(RANLIB) $@

$(OBJDIR)/%.o: $(SRCDIR)/%.c | $(OBJDIR)
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

$(OBJDIR_PCC)/%.o: $(SRCDIR)/%.c | $(OBJDIR_PCC)
	$(PCC) --cpp-arg=-I$(CURDIR)/include --cpp-arg=-I$(CURDIR)/src --emit-obj $@ $<

$(OBJDIR_PCC):
	mkdir -p $(OBJDIR_PCC)

# Phase 4c track: runtime-high modules ported from C to pcc-compilable
# Python under py/. pcc emits LLVM IR for each module; we strip the
# auto-generated main() (libraries have no entry point) and let clang
# finish the .o. The resulting archive starts from the pcc-C archive
# and then REPLACES each .o listed in PY_MODULES with the Python-built
# one, so every other runtime module keeps its pcc-C implementation.
$(LIB_PCC_PY): $(LIB_PCC) $(OBJS_PY)
	cp $(LIB_PCC) $@
	$(AR) d $@ $(addsuffix .o,$(PY_MODULES))
	$(AR) r $@ $(OBJS_PY)
	$(RANLIB) $@

# Compatibility fallback archive for bootstrap builds that still emit
# py_cpy_* calls. It keeps the pcc-Python runtime replacement archive
# as the base and adds only the CPython bridge object.
$(LIB_PCC_PY_LIBPYTHON): $(LIB_PCC_PY) $(OBJDIR_LIBPY)/py_libpython.o
	cp $(LIB_PCC_PY) $@
	-$(AR) d $@ py_libpython.o
	$(AR) r $@ $(OBJDIR_LIBPY)/py_libpython.o
	$(RANLIB) $@

$(OBJDIR_LIBPY)/py_libpython.o: $(SRCDIR)/py_libpython.c | $(OBJDIR_LIBPY)
	$(CC) $(CPPFLAGS) $(CFLAGS) -DPCC_WITH_LIBPYTHON=1 $(shell python3-config --includes) $(INCLUDES) -c $< -o $@

$(OBJDIR_LIBPY):
	mkdir -p $(OBJDIR_LIBPY)

$(OBJDIR_PY)/%.o: $(PYDIR)/%.py | $(OBJDIR_PY)
	$(PCC) --emit-llvm=$(OBJDIR_PY)/$*.ll $<
	awk 'BEGIN{s=0} /^define i32 @main\(/{s=1} s==1 && /^}/{s=0; next} s==0' \
		$(OBJDIR_PY)/$*.ll > $(OBJDIR_PY)/$*.nomain.ll
	$(CLANG) -c $(OBJDIR_PY)/$*.nomain.ll -o $@

$(OBJDIR_PY):
	mkdir -p $(OBJDIR_PY)

clean:
	rm -rf $(OBJDIR) $(LIB)

distclean: clean
	rm -rf $(OBJDIR_PCC) $(LIB_PCC) $(OBJDIR_PY) $(LIB_PCC_PY) \
		$(OBJDIR_LIBPY) $(LIB_PCC_PY_LIBPYTHON)

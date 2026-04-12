# pcc/py_runtime/Makefile
#
# Build libpy_runtime.a, the C runtime for pcc's Python frontend.
#
# Public header:  include/py_runtime.h
# Source:         src/*.c
# Output:         libpy_runtime.a (in this directory)
#
# Usage:
#   make              - build libpy_runtime.a
#   make clean        - remove object files and the archive

CC       ?= cc
AR       ?= ar
RANLIB   ?= ranlib

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

SRCDIR  = src
OBJDIR  = build

SRCS = \
	$(SRCDIR)/py_obj.c \
	$(SRCDIR)/py_obj_ops.c \
	$(SRCDIR)/py_int.c \
	$(SRCDIR)/py_str.c \
	$(SRCDIR)/py_list.c \
	$(SRCDIR)/py_tuple.c \
	$(SRCDIR)/py_dict.c \
	$(SRCDIR)/py_set.c \
	$(SRCDIR)/py_class.c \
	$(SRCDIR)/py_exc.c \
	$(SRCDIR)/py_libpython.c \
	$(SRCDIR)/py_print.c \
	$(SRCDIR)/py_obj_stubs.c

OBJS = $(patsubst $(SRCDIR)/%.c,$(OBJDIR)/%.o,$(SRCS))

LIB  = libpy_runtime.a

.PHONY: all clean

all: $(LIB)

$(LIB): $(OBJS)
	$(AR) rcs $@ $^
	$(RANLIB) $@

$(OBJDIR)/%.o: $(SRCDIR)/%.c | $(OBJDIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(INCLUDES) -c $< -o $@

$(OBJDIR):
	mkdir -p $(OBJDIR)

clean:
	rm -rf $(OBJDIR) $(LIB)

# Build the pcc-native-tls-v1 OpenSSL provider.
#
# OpenSSL >= 3.0.0 is required.  Discovery is explicit and happens only at
# build time; the gateway itself opens the resulting absolute library path.
# Examples:
#   make
#   make OPENSSL_PREFIX=/opt/openssl-3
#   make OUT_DIR=/tmp/pcc-tls-provider

CC ?= cc
PKG_CONFIG ?= pkg-config
OPENSSL_PREFIX ?=
OUT_DIR ?= build

CPPFLAGS ?=
CFLAGS ?= -O2 -fPIC -fvisibility=hidden -Wall -Wextra -Werror -std=c11
LDFLAGS ?=
OPENSSL_RUNTIME_PATH_FLAGS ?=

ifeq ($(strip $(OPENSSL_PREFIX)),)
OPENSSL_CFLAGS ?= $(shell $(PKG_CONFIG) --cflags openssl)
OPENSSL_LIBS ?= $(shell $(PKG_CONFIG) --libs openssl)
else
OPENSSL_CFLAGS ?= -I$(OPENSSL_PREFIX)/include
OPENSSL_LIBS ?= -L$(OPENSSL_PREFIX)/lib -lssl -lcrypto
OPENSSL_RUNTIME_PATH_FLAGS := -Wl,-rpath,$(OPENSSL_PREFIX)/lib
endif

UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
LIB_NAME := libpcc_tls_openssl.dylib
SHARED_FLAGS := -dynamiclib -Wl,-install_name,@rpath/$(LIB_NAME)
else
LIB_NAME := libpcc_tls_openssl.so
SHARED_FLAGS := -shared -Wl,-soname,$(LIB_NAME)
endif

TARGET := $(OUT_DIR)/$(LIB_NAME)
SOURCE := openssl_provider.c
ABI_HEADER := ../include/pcc_tls_provider_v1.h

.PHONY: all contract clean

all: $(TARGET)

$(TARGET): $(SOURCE) $(ABI_HEADER)
	mkdir -p $(OUT_DIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(OPENSSL_CFLAGS) \
		-I../include $(SHARED_FLAGS) $(OPENSSL_RUNTIME_PATH_FLAGS) \
		$(LDFLAGS) -o $@ $(SOURCE) \
		$(OPENSSL_LIBS)

contract:
	@echo provider=pcc-native-tls-v1
	@echo implementation=pcc-openssl-3-provider-v1
	@echo minimum_openssl=3.0.0
	@echo output=$(TARGET)

clean:
	rm -f $(TARGET)

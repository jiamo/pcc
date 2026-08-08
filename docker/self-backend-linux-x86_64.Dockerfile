FROM --platform=linux/amd64 python:3.13-bookworm

ENV DEBIAN_FRONTEND=noninteractive

# bookworm ships clang-14, which predates LLVM's opaque `ptr` type and rejects
# pcc's emitted IR with "expected type". Pull clang-16 from LLVM's own apt repo.
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget gnupg ca-certificates \
    && wget -qO- https://apt.llvm.org/llvm-snapshot.gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/llvm.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/llvm.gpg] http://apt.llvm.org/bookworm/ llvm-toolchain-bookworm-16 main" \
        > /etc/apt/sources.list.d/llvm16.list \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        clang-16 \
        make \
        pkg-config \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/clang-16 /usr/local/bin/clang \
 && ln -sf /usr/bin/clang-16 /usr/local/bin/cc

RUN pip install --no-cache-dir uv

WORKDIR /workspace

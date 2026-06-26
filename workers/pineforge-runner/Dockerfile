FROM node:22-bookworm-slim AS build

WORKDIR /app
COPY package*.json tsconfig.json ./
RUN npm install
COPY src ./src
RUN npm run build && npm prune --omit=dev

FROM debian:bookworm-slim AS pineforge-engine

ARG PINEFORGE_ENGINE_REF=v0.10.10
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      cmake \
      g++ \
      git \
      libeigen3-dev \
      make \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN set -eux; \
    git clone https://github.com/pineforge-4pass/pineforge-engine.git; \
    cd pineforge-engine; \
    if git fetch --depth 1 origin "${PINEFORGE_ENGINE_REF}"; then \
      git checkout FETCH_HEAD; \
    else \
      git checkout "${PINEFORGE_ENGINE_REF}"; \
    fi; \
    cmake -B build \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/opt/pineforge \
      -DPINEFORGE_BUILD_TESTS=OFF \
      -DPINEFORGE_BUILD_TUTORIAL=OFF \
      -DPINEFORGE_BUILD_CORPUS_STRATEGIES=OFF \
    ; \
    cmake --build build --target pineforge -j"$(nproc)"; \
    cmake --install build; \
    cp VERSION /opt/pineforge/VERSION

FROM node:22-bookworm-slim

WORKDIR /app
ARG PINEFORGE_CODEGEN_VERSION=0.7.5
ARG PINEFORGE_ENGINE_VERSION=0.10.10
ENV NODE_ENV=production \
    PINEFORGE_PREFIX=/opt/pineforge \
    PINEFORGE_INCLUDE_DIR=/opt/pineforge/include \
    PINEFORGE_LIB_DIR=/opt/pineforge/lib \
    PINEFORGE_NATIVE_COMMAND=/opt/pineforge-codegen/bin/python3 \
    PINEFORGE_NATIVE_ARGS=/app/bin/pineforge-native-runner.py \
    PINEFORGE_ENGINE_VERSION=${PINEFORGE_ENGINE_VERSION} \
    PINEFORGE_CODEGEN_VERSION=${PINEFORGE_CODEGEN_VERSION}
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      g++ \
      libeigen3-dev \
      python3 \
      python3-pip \
      python3-venv \
    && python3 -m venv /opt/pineforge-codegen \
    && /opt/pineforge-codegen/bin/pip install --no-cache-dir "pineforge-codegen==${PINEFORGE_CODEGEN_VERSION}" \
    && rm -rf /var/lib/apt/lists/*
ENV PATH="/opt/pineforge-codegen/bin:${PATH}"
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY --from=pineforge-engine /opt/pineforge /opt/pineforge
COPY bin ./bin
RUN chmod +x /app/bin/pineforge-native-runner.py
EXPOSE 8080
CMD ["node", "dist/server.js"]

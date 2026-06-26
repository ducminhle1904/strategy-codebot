# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS builder

WORKDIR /worker
COPY package.json tsconfig.json ./
RUN --mount=type=cache,target=/root/.npm npm install
COPY src ./src
RUN npm run build

FROM node:22-bookworm-slim AS runtime

ENV NODE_ENV=production \
    STRATEGY_CODEBOT_API_ARTIFACT_ROOT=/var/lib/strategy-codebot/artifacts

WORKDIR /worker
COPY --from=builder /worker/package.json ./package.json
COPY --from=builder /worker/node_modules ./node_modules
COPY --from=builder /worker/dist ./dist

CMD ["node", "dist/index.js"]

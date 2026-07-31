# syntax=docker/dockerfile:1
# Next.js 15 standalone image. Build for linux/amd64 — the AKS node pool is x86 B-series.
#
#   docker build --platform linux/amd64 -t aisdlcacr2026.azurecr.io/frontend:dev .

FROM node:20-alpine AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --ignore-scripts

FROM node:20-alpine AS build
WORKDIR /app
RUN corepack enable
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* are inlined into the client bundle at BUILD time — a ConfigMap can't
# change them afterwards. To flip the app off mocks, rebuild with different --build-arg.
ARG NEXT_PUBLIC_AUTH_MODE=mock
ARG NEXT_PUBLIC_API_MOCKS=enabled
ARG NEXT_PUBLIC_API_BASE=/api
ARG NEXT_PUBLIC_ENABLE_OIDC=false
ARG NEXT_PUBLIC_DISABLE_STREAMS=false
ENV NEXT_PUBLIC_AUTH_MODE=$NEXT_PUBLIC_AUTH_MODE \
    NEXT_PUBLIC_API_MOCKS=$NEXT_PUBLIC_API_MOCKS \
    NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE \
    NEXT_PUBLIC_ENABLE_OIDC=$NEXT_PUBLIC_ENABLE_OIDC \
    NEXT_PUBLIC_DISABLE_STREAMS=$NEXT_PUBLIC_DISABLE_STREAMS \
    NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM node:20-alpine AS run
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
RUN addgroup -g 1001 -S nodejs && adduser -u 1001 -S nextjs -G nodejs
COPY --from=build /app/public ./public
COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]

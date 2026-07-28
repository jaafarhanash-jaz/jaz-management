# syntax=docker/dockerfile:1

# ---- Build stage ----
FROM node:20-alpine AS builder

WORKDIR /app

# Corepack (bundled with Node 20) reads the "packageManager" field in
# package.json and fetches the exact pinned Yarn Classic version - no
# separate global Yarn install needed.
RUN corepack enable

COPY package.json yarn.lock ./
RUN yarn install --frozen-lockfile

COPY . .

# Baked into the static JS bundle at build time (CRA inlines REACT_APP_*
# vars - they can't be changed later without rebuilding). Left empty by
# default so axios's baseURL resolves to the relative path "/api",
# which nginx.conf reverse-proxies to the backend container, same-origin,
# from the browser's point of view - no CORS needed for normal traffic.
# Override with --build-arg only for a split-domain deployment where the
# frontend and backend are served from different hosts.
ARG REACT_APP_BACKEND_URL=""
ENV REACT_APP_BACKEND_URL=${REACT_APP_BACKEND_URL}

# CRA's build treats every ESLint *warning* as a hard build failure when
# CI=true (many CI/build systems set this automatically) - there's a
# pre-existing, harmless react-hooks/exhaustive-deps warning in the
# codebase today that would otherwise break this build non-deterministically
# depending on the environment. Pinning it off here is a build-config
# safeguard, not an application change.
ENV CI=false

RUN yarn build

# ---- Runtime: nginx serving the static build ----
FROM nginx:stable-alpine AS runtime

COPY --from=builder /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# 127.0.0.1, not localhost: verified directly on production that this
# image's musl libc (Alpine) resolves "localhost" to ::1 first, and nginx
# (plain `listen 80;`, IPv4 only) refuses that connection - the healthcheck
# was failing every single check (FailingStreak in the thousands) with
# "Connection refused" even though real traffic, which reaches nginx over
# IPv4 via Docker's port publishing, was served correctly the whole time.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1

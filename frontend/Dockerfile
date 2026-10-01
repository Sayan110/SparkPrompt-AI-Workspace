# syntax=docker/dockerfile:1
# SparkPrompt Next.js — production image (Phase 4D, Steps 8/9).
#
# `next build` runs at IMAGE build time; the container only ever runs
# `npm run start` (never `next dev`). The public API origin is injected as a
# build ARG — NEXT_PUBLIC_* values are public by contract; server secrets
# (SESSION_SECRET, DATABASE_URL, provider keys) never enter this image.
FROM node:24-alpine

WORKDIR /app
# The image runs as the non-root "node" user; npm must be able to create
# /app/node_modules and the build cache inside /app.
RUN chown node:node /app

# Lockfile install for reproducible builds; owned by the non-root user.
COPY --chown=node:node package.json package-lock.json ./
USER node
RUN npm ci

COPY --chown=node:node . .

# Public API origin: set to the reverse-proxy origin in production so the
# browser calls /api/* same-origin (session cookie stays SameSite=Lax).
ARG NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_TELEMETRY_DISABLED=1

RUN npm run build

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

EXPOSE 3000

# Liveness: the production server answers HTTP on the app root.
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD wget -q -O /dev/null "http://127.0.0.1:3000/" || exit 1

CMD ["npm", "run", "start"]

# Multi-stage Next.js production build.
#
# Stage 1 (deps)    — install node_modules
# Stage 2 (builder) — run next build, produce .next/standalone
# Stage 3 (runner)  — minimal Node.js image, non-root, read-only-friendly
#
# The build script in package.json runs `tsx lib/db/migrate && next build`.
# The migration step requires a live database and is not needed for Docker
# builds — the Python API owns all database operations. We invoke next build
# directly to skip it.

# ── Stage 1: dependencies ─────────────────────────────────────────────────────
FROM node:20-alpine AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --legacy-peer-deps --quiet

# ── Stage 2: build ────────────────────────────────────────────────────────────
FROM node:20-alpine AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Build the app. output: 'standalone' in next.config.ts creates
# .next/standalone — a self-contained Node.js server bundle.
RUN npx next build

# ── Stage 3: production runner ────────────────────────────────────────────────
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000

RUN addgroup --system --gid 1001 nodejs \
 && adduser  --system --uid 1001 nextjs

# Standalone bundle includes server.js + all required node_modules
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
# Static assets (CSS, JS chunks, images) must be copied separately
COPY --from=builder --chown=nextjs:nodejs /app/.next/static   ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public         ./public

USER nextjs

EXPOSE 3000

# server.js is the standalone Next.js server entry point
CMD ["node", "server.js"]

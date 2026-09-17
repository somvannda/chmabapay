# The Next.js apps. One Dockerfile, two images, differing only by `APP` — the
# website and the admin console are separate workspaces in the same pnpm
# monorepo and share `web/shared`, so they must build from the repository root.
#
# Build context is the repository root (see docker-compose.yml):
#   docker build -f web/Dockerfile --build-arg APP=landing .
#
# `next start` behind the proxy rather than `output: "standalone"`. Standalone
# would trim the image, but it relocates the traced server out of the pnpm
# workspace and the workspace symlinks are exactly what it is worst at — a build
# that fails there looks like a source error. Image size is not the constraint;
# a build that either works or does not is.

FROM node:20-alpine

# Pinned to the major that wrote `pnpm-lock.yaml` (lockfileVersion 9.0). A newer
# pnpm would refuse `--frozen-lockfile` on a lockfile it did not write, which is a
# build failure that reads as a dependency problem.
RUN npm install --global pnpm@9

ENV NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

WORKDIR /app

# `NODE_ENV=production` above is deliberate at run time and a trap at install
# time: pnpm 9 reads it and silently skips devDependencies. `next` is a real
# dependency and survives, so the install looks fine — but `typescript` is a
# devDependency, and without it Next cannot load tsconfig `paths`. Every `@/`
# import then fails with "Can't resolve '@/...'" over files that are sitting
# right there, while `tailwindcss` fails separately on the PostCSS step. Two
# symptoms, one cause, no mention of the missing package. `--prod=false` is
# spelled out rather than inferred so the next pnpm major cannot reintroduce it.

# --------------------------------------------------------------------------- #
# Manifests first: the dependency layer then caches independently of source, and
# editing a page does not re-resolve the tree.
# --------------------------------------------------------------------------- #
# All workspace manifests are needed, not just the one being built — pnpm resolves
# the whole workspace and `--frozen-lockfile` compares against all of it.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY web/landing/package.json web/landing/
COPY web/admin/package.json web/admin/
COPY web/user/package.json web/user/
COPY web/shared/package.json web/shared/

RUN --mount=type=cache,id=pnpm-store,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile --prod=false

# --------------------------------------------------------------------------- #
# Source
# --------------------------------------------------------------------------- #
COPY web ./web

ARG APP=landing
ENV APP=${APP}

# Declared, and that declaration is load-bearing: `docker build --build-arg X=...`
# silently *ignores* X unless the Dockerfile consumes it with `ARG X`. Without this
# line compose passes the value, the build succeeds with a "not consumed" warning
# nobody reads, and the app falls back to `http://127.0.0.1:8000` baked into
# `routes-manifest.json` — which is the 500 this stack shipped with. An `ARG` is
# visible to `RUN` in this stage, which is where `next build` evaluates `rewrites()`.
ARG NEXT_PUBLIC_API_URL

RUN pnpm --filter "@chmabapay/${APP}" build

# Non-root. The Next build writes nothing at runtime, so owning the tree is enough.
RUN addgroup -S chmaba && adduser -S -G chmaba -u 10001 chmaba \
    && chown -R chmaba:chmaba /app
USER chmaba

# The port comes from each app's own `start` script (landing 3001, admin 3002,
# user 3000), so it is declared per service in docker-compose.yml rather than
# hardcoded here where it would be wrong for one of them.

# `sh -c` so `${APP}` is expanded at run time; the exec form would not expand it.
CMD ["sh", "-c", "pnpm --filter @chmabapay/${APP} start"]

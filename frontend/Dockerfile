# PLACEHOLDER SKELETON (GEO-1).
# The real React + Vite + MapLibre app is GEO-22; the production multi-stage build with
# build-time Vite env injection is GEO-33. This stub compiles a placeholder dist/ and
# copies it to /out (the web_dist volume) so the build→serve wiring is exercised.
FROM node:22-alpine

WORKDIR /app
COPY package.json ./
# No lockfile / deps yet (GEO-22 adds Vite, MapLibre, deck.gl). `npm ci` lands then.
RUN npm install --no-audit --no-fund || true
COPY . .

RUN npm run build

# Emit the build output into the shared volume mounted at /out (web serves it).
CMD ["sh", "-c", "cp -r dist/* /out/"]

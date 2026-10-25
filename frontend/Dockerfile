# The Mini App ships inside the Caddy image that serves it.
#
# Stage 1 builds the React app with Node; stage 2 is plain Caddy with only
# the built files copied in. Node, node_modules and the source never reach
# the server's running container - and the server itself needs no Node at
# all, since `docker compose build` does this as part of every deploy.

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
# npm ci, not npm install: installs exactly what package-lock.json says, so
# the server builds the same versions that were tested on the laptop.
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM caddy:2
COPY --from=build /app/dist /srv/miniapp

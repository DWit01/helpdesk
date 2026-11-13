FROM node:24-slim AS builder

WORKDIR /build

COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json ./
COPY src/ ./src/
RUN npm run build

FROM docker.io/nginxinc/nginx-unprivileged:stable-alpine AS runtime

COPY index.html styles.css config.js /usr/share/nginx/html/
COPY svg/ /usr/share/nginx/html/svg/
COPY --from=builder /build/dist/ /usr/share/nginx/html/dist/
COPY --from=builder /build/node_modules/keycloak-js/ /usr/share/nginx/html/node_modules/keycloak-js/

EXPOSE 8080

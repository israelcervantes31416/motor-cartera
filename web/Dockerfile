# La superficie web: la SPA construida con Node y servida, con su proxy de solo lectura, por nginx.
# Dos etapas: la primera construye (npm ci con el package-lock.json, vite build) y la segunda solo
# lleva los archivos estaticos y la configuracion de nginx. Las dos imagenes, fijadas por su
# version exacta y su digest: la de Node es la misma version mayor de .node-version.

FROM node:24.21.0-alpine3.24@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS construccion

WORKDIR /web
COPY package.json package-lock.json .npmrc ./
RUN npm ci --ignore-scripts
COPY . .
RUN npm run build

FROM nginx:1.30.6-alpine3.24@sha256:970364228b1e4f932abc22d321932816782cca8a20b85a77552a7cfe9acfb3b5

# Solo las variables MC_* se sustituyen en la plantilla: las de nginx ($uri, $host...) no.
ENV NGINX_ENVSUBST_FILTER=^MC_ \
    MC_API_UPSTREAM=api:8000

COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY nginx/rutas-api.conf /etc/nginx/rutas-api.conf
RUN rm -f /etc/nginx/conf.d/default.conf
COPY --from=construccion /web/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=3s --retries=12 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/salud-web || exit 1

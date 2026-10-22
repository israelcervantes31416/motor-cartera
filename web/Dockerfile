# La superficie web: la SPA construida con Node y servida, con su proxy, por nginx sin privilegios.
# Dos etapas: la primera construye (npm ci con el package-lock.json, vite build) y la segunda solo
# lleva los archivos estaticos y la configuracion de nginx. Las dos imagenes, fijadas por su version
# exacta y su digest: la de Node es la misma version mayor de .node-version.
#
# Una sola imagen para los dos perfiles: NGINX_ENVSUBST_TEMPLATE_DIR elige la plantilla. Por omision
# la de desarrollo (http, proxy con la clave de desarrollo); el compose de produccion elige la de
# produccion (TLS, sesion OIDC por oauth2-proxy, limites). nginx corre como el usuario 101, en los
# puertos 8080 y 8443, y la configuracion renderizada va a /etc/nginx/conf.d, que el compose de
# produccion monta en memoria con el resto del sistema de archivos de solo lectura.

FROM node:26.11.1-alpine3.24@sha256:143494b1da2945f061539253adc65e4f1569ddf07da2d384c022c791a9d90a4a AS construccion

WORKDIR /web
COPY package.json package-lock.json .npmrc ./
RUN npm ci --ignore-scripts
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.30.6-alpine@sha256:782bb89768cc87b7f95c18b95baa1af250e5d451f26794f59363540161147c01

# El digest fija la base; las correcciones de seguridad que Alpine publico despues se aplican al
# construir (el CI escanea la imagen con Trivy y falla con una CVE alta o critica ya corregida).
USER root
RUN apk upgrade --no-cache

# Solo las variables MC_* se sustituyen en la plantilla: las de nginx ($uri, $host...) no.
ENV NGINX_ENVSUBST_FILTER=^MC_ \
    NGINX_ENVSUBST_TEMPLATE_DIR=/etc/nginx/plantillas/desarrollo \
    NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx/conf.d \
    NGINX_ENTRYPOINT_QUIET_LOGS=1 \
    MC_API_UPSTREAM=api:8000

COPY nginx/default.conf.template /etc/nginx/plantillas/desarrollo/default.conf.template
COPY nginx/produccion.conf.template /etc/nginx/plantillas/produccion/default.conf.template
COPY nginx/rutas-api.conf /etc/nginx/rutas-api.conf
COPY nginx/cabeceras.conf nginx/cabeceras-tls.conf nginx/proxy-oauth2.conf /etc/nginx/
COPY --chmod=0755 nginx/15-secretos.envsh /docker-entrypoint.d/15-secretos.envsh
COPY --from=construccion /web/dist /usr/share/nginx/html

USER 101
EXPOSE 8080 8443

HEALTHCHECK --interval=5s --timeout=3s --retries=12 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/salud-web || exit 1

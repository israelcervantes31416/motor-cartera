# La cadena de suministro (v0.18)

Qué entra al sistema que no escribimos nosotros (dependencias, imágenes, acciones de GitHub,
proveedores de OpenTofu), cómo se fija, cómo se revisa y qué se acepta. El porqué, en las decisiones
298 a 300 de [decisiones.md](decisiones.md).

## Todo fijado

| Qué | Cómo se fija | Quién lo revisa |
| --- | --- | --- |
| Dependencias de Python | `uv.lock`, con hashes; `uv sync --locked` falla si el lock no corresponde al `pyproject.toml` | el CI (`--locked`), pip-audit |
| Dependencias de la app web | `web/package-lock.json`, con hashes; `npm ci --ignore-scripts` (sin scripts de instalación) | el CI, npm audit |
| Imágenes base (`FROM`) | por digest (`@sha256:...`) en `Dockerfile`, `web/Dockerfile` e `infra/respaldo/Dockerfile` | `scripts/verificar_cadena_de_suministro.py` |
| Imágenes de los compose | por digest; las que el compose construye llevan `build:` | el mismo script |
| Imágenes que corren los scripts y el CI | por digest: k6, OpenTofu, Trivy, gitleaks, OSRM | el mismo script |
| Acciones de GitHub | por el SHA de su commit, con su versión en un comentario (`# v7.0.1`) | el mismo script |
| Proveedores de OpenTofu | `infra/gcp/.terraform.lock.hcl`, con hashes; el CI inicializa con `-lockfile=readonly` | el CI |
| `uv` y Node | `uv` 0.12.21 (el CI, el Dockerfile y el lock), Node de `web/.node-version` | el CI |

El script también exige que cada workflow declare sus permisos (`contents: read` por omisión;
`id-token: write` solo en el job de despliegue), que ninguno use `write-all` y que cada checkout deje
la credencial fuera del disco (`persist-credentials: false`). Corre en el job `archivos` del CI, y
`tests/test_cadena_de_suministro.py` prueba cada regla con archivos de ejemplo.

## Lo que se escanea

| Escaneo | Sobre qué | Política |
| --- | --- | --- |
| **pip-audit** 2.10.1 | las dependencias exactas del `uv.lock` (todas las extras, con hashes) | una vulnerabilidad conocida detiene el CI |
| **npm audit** | las dependencias de producción de la app web | una alta o crítica detiene el CI |
| **Trivy** 0.75.0 | las tres imágenes propias (`motor-cartera:produccion`, `motor-cartera-web:produccion`, `motor-cartera-respaldo:local`) | una vulnerabilidad alta o crítica **con corrección publicada** detiene el CI; las que no tienen corrección se reportan |
| **Trivy** | las imágenes de terceros del camino de una petición (PostGIS, Dex, oauth2-proxy, Valkey, OSRM) | informe en el resumen del run; no detiene: se atienden actualizando su digest |
| **gitleaks** 8.30.1 | el historial completo del repositorio, en cada run | un hallazgo detiene el CI; dos excepciones literales y justificadas en `.gitleaks.toml` |

**Estado al cerrar v0.18 (2026-10-18):** pip-audit sin vulnerabilidades conocidas en 226
dependencias exactas; npm audit, 0 (de producción y todas); Trivy, 0 altas o críticas con corrección
en las tres imágenes propias; gitleaks, 158 commits sin hallazgos (con las dos excepciones).

**Las dos excepciones de gitleaks:** la clave de desarrollo documentada (`clave-local-de-desarrollo`,
pública a propósito para el compose de desarrollo; producción se niega a arrancar con `MC_API_KEY`) y
los JWT falsos de una prueba de redacción (firma literal `firmaDeLaPrueba`), solo en ese archivo y
solo con ese patrón.

### Las imágenes de Alpine se actualizan al construir

Al cerrar v0.18, Trivy encontró en las imágenes de nginx y de PostGIS (fijadas por digest, que es su
última versión publicada) vulnerabilidades altas ya corregidas en Alpine (libexpat, libx11,
util-linux, pcre2, libtiff...), y en la de respaldo, `gosu` compilado con un Go viejo (una crítica y
24 altas). La base sigue fijada por su digest, y las correcciones de seguridad de Alpine se aplican al
construir (`apk upgrade --no-cache`); la imagen de respaldo no lleva `gosu` (lo usa el entrypoint de
PostgreSQL para cambiar de usuario, y ahí no hay servidor). Con eso, las tres quedaron en 0.

## El SBOM

El job `produccion` del CI genera el SBOM de cada imagen propia con Trivy (CycloneDX 1.7) y lo deja
como artefacto del run (`produccion-evidencia`): `sbom-api.cdx.json` (150 componentes al cerrar
v0.18), `sbom-web.cdx.json` (71) y `sbom-respaldo.cdx.json` (127).

## Dependabot

`.github/dependabot.yml` propone actualizaciones semanales de Python (`uv`), de la app web (`npm`),
de las acciones, de los digests de las imágenes (`docker` y `docker-compose`) y, mensuales, del
proveedor de OpenTofu. Cada fuente espera **7 días** después de una versión nueva antes de
proponerla (`cooldown`): una versión comprometida suele descubrirse en esos días. Cada PR pasa por el
CI completo.

## Qué es reproducible

- **Las dependencias**: exactamente las mismas versiones y bytes (los hashes de los lockfiles), en el
  CI, en la imagen y en una laptop.
- **El punto de partida de cada imagen**: su base por digest.
- **El build de la app web**: Vite produce los mismos archivos con los mismos nombres (su hash es de
  su contenido) desde el mismo lock.
- **Lo que no**: una imagen **bit por bit**. Las capas llevan fechas, y los paquetes de Alpine se
  actualizan al construir: dos construcciones en días distintos pueden diferir en esos paquetes. Lo
  que se despliega se identifica por su digest (el que se probó es el que corre), no por volver a
  construirlo.

## Cómo se agrega una dependencia

1. `uv add <paquete>` (o `npm install --save-exact` en `web/`), que actualiza el lock con sus hashes.
2. El CI la audita (pip-audit o npm audit) y corre todo con ella.
3. Si es una imagen, va con su digest: `docker buildx imagetools inspect <imagen>:<etiqueta>`.
4. Si es una acción, con el SHA del commit de su versión exacta y la versión en un comentario.

# Las dependencias en v1.0

Estado de la cola de Dependabot después de publicar v1.0.0 (2026-10-21). Este documento es una
**foto de triage**, no una orden de actualización: ningún PR se fusiona por reflejo y una cola abierta
no invalida una release estable. La política está en
[cadena-de-suministro.md](cadena-de-suministro.md#dependabot).

La rama estable sigue en Python 3.12 como runtime, PostgreSQL 16/PostGIS 3.5 y Node 24. Las versiones
3.13 y 3.14 de Python se prueban en la matriz de compatibilidad, pero no sustituyen por sí solas al
runtime de producción.

## Fusionados desde v0.18.0

| PR | Componente | Cambio | Por qué |
| --- | --- | --- | --- |
| #21 | oauth2-proxy | v7.15.4 → v7.15.5 | **Seguridad**, en v0.18.1: GHSA-hhqp-vx7f-5c6m sí aplicaba |
| #23 | astral-sh/setup-uv | 10.2.0 → 10.3.0 | Acción de CI, con la versión de uv todavía fijada explícitamente |
| #20 | Jaeger | 2.21.0 → 2.22.0 | Menor del perfil de observabilidad |
| #36 | Vite | 8.3.2 → 8.3.4 | **Seguridad**: GHSA-698v-4r3x-3vcv en el servidor de desarrollo de Windows |

## Cola abierta actual

Los PR antiguos que Dependabot cerró y regeneró al entrar la política de v1 ya no se listan como
pendientes. En particular, #26, #31 y #37 fueron reemplazados por PR nuevos; conservar sus números
como si siguieran abiertos hacía que este documento quedara desfasado.

| PR | Componente | Actual → propuesta | Base/CI observado | Decisión |
| --- | --- | --- | --- | --- |
| #22 | OTel Collector | 0.161.0 → 0.162.0-386 | detrás de v1; último CI verde | **No fusionar así**: `-386` es la imagen linux/386; la variante esperada es la multiplataforma |
| #24 | OSRM | v26.9.0 → 26.10.0 | detrás de v1; compose/pruebas rojos | Migración coordinada: imagen, red preparada y contrato deben moverse juntos |
| #25 | Valkey | 8.1.10 → 9.1.2 | detrás de v1; último CI verde | Mayor; evaluar como migración de sesiones, no como mantenimiento automático |
| #27 | Python (imagen) | 3.12 → 3.14 | detrás de v1; producción roja | No sustituye al runtime 3.12; la compatibilidad 3.14 ya se prueba aparte |
| #28 | PostGIS (respaldo) | 16 → 17 | detrás de v1; producción roja | PostgreSQL/PostGIS 17 requiere migración coordinada con el servidor y restore |
| #29 | nginx-unprivileged | estable 1.30.6 → mainline 1.31.6 | detrás de v1; último CI verde | No cambiar de rama estable a mainline por una actualización automática |
| #30 | Node (imagen) | 24.21 → 26.11 | detrás de v1; compose rojo | Mayor; cambia también `engines`, `.node-version` y tipos |
| #39 | Grafana | 13.1.8 → 13.2.4 | sobre v1; CI completo verde | Candidato compatible para una siguiente actualización de mantenimiento |
| #40 | PostGIS (compose) | 16-3.5 → 17-3.5 | sobre v1; compose rojo | Mismo cambio mayor que #28: no se hace por piezas |
| #41 | uv (imagen) | 0.12.21 → 0.13.4 | sobre v1; CI completo verde | Prioridad de mantenimiento, pero **coordinada** con todos los pins de uv; no fusionar solo el Dockerfile |
| #42 | grupo web | 8 actualizaciones compatibles | sobre v1; CI completo verde | Candidato para mantenimiento; sin los majors que el grupo anterior mezclaba |
| #43 | grupo Python | 9 actualizaciones | sobre v1; `pruebas` rojo | No fusionar: SQLModel 0.0.48 expone incompatibilidades de tipos que Pyright detecta |

## Notas de los casos que requieren cuidado

- **#22.** `0.162.0-386` no es la variante normal del collector para este proyecto. Que un runner
  pueda ejecutarla no es razón para publicarla.
- **#24.** OSRM está fijado en los compose y en la versión con que se prepara la red vial. Cambiarlo
  exige volver a preparar y verificar esa red.
- **#27.** Python 3.14 ya forma parte de la matriz ligera de compatibilidad. Eso no convierte una
  actualización de la imagen de producción en un cambio automático.
- **#28 y #40.** El cliente de respaldo y el servidor deben permanecer compatibles. PostgreSQL 17
  será una migración explícita, no dos merges independientes.
- **#29.** La rama 1.31 es mainline; el proyecto no cambia de canal de nginx únicamente porque
  Dependabot encuentre una etiqueta más nueva.
- **#30.** Node 26 es una migración de runtime del frontend. La versión estable de v1 permanece en
  Node 24 hasta que ese cambio se haga deliberadamente.
- **#41.** Las notas de uv 0.13.4 incluyen GHSA-hq85-5cpj-mr62 y una actualización de certificados
  raíz. El proyecto fija uv en el Dockerfile y en varios workflows, por lo que la actualización se
  debe hacer en conjunto y volver a pasar el CI completo; el PR automático solo cambia el
  Dockerfile.
- **#42.** Es el resultado de la nueva política que deja fuera los majors del grupo web. Su CI
  completo está verde sobre v1, pero una release estable no se mueve solo para vaciar la cola.
- **#43.** El grupo Python nuevo ya no incluye PyArrow 26, pero sigue fallando por el cambio de tipos
  de SQLModel 0.0.48. Se separa o corrige antes de cualquier merge.

## Seguridad y criterio de actualización

Las alertas de Dependabot, sus actualizaciones de seguridad, CodeQL, pip-audit, npm audit, Trivy y
gitleaks siguen siendo los controles del proyecto. Una actualización de seguridad **aplicable** se
atiende con prioridad; un PR de versión normal, incluso verde, no se vuelve obligatorio por existir.

Desde v1.0, Dependabot agrupa parches/menores compatibles cuando tiene sentido y deja los mayores
separados. Los mayores no se ignoran para siempre: se tratan como migraciones explícitas bajo
[versioning.md](versioning.md).

## Regla operativa

- No auto-merge.
- No perseguir “0 PR abiertos”.
- No fusionar un PR que esté detrás de `main` usando resultados viejos como evidencia suficiente.
- No dividir una migración acoplada solo para aceptar PRs automáticos.
- Antes de cada merge: actualizar a la cabeza de `main`, ejecutar los checks actuales y revisar el
  diff real.

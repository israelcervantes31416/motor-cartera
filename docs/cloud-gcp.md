# La arquitectura de referencia en Google Cloud (v0.18)

Cómo correría motor-cartera en Google Cloud, escrito en OpenTofu (`infra/gcp`). **Es una
referencia: nunca se aplicó y este repositorio no la aplica.** Se valida en cada run del CI sin
credenciales ni red de Google: `tofu fmt`, `tofu init` sin backend y con el lock de solo lectura,
`tofu validate`, el plan de los dos entornos de ejemplo sin estado ni credenciales y la política de
`scripts/verificar_iac.py`. Aplicarla crea recursos con costo en un proyecto real; eso es una
decisión humana, con su proyecto, su dominio, su IdP y sus credenciales.

## La forma

```text
Internet ──▶ balanceador HTTPS externo global (IP fija, certificado administrado, TLS 1.2+)
              │  Cloud Armor: limite por IP del cliente, reglas OWASP en vista previa
              ▼
        Cloud Run "web" (solo el balanceador entra: ingress INTERNAL_LOAD_BALANCER)
        ┌───────────────────────────────────────────────────────────────┐
        │ edge (nginx + la SPA) ──localhost──▶ oauth2-proxy ──▶ IdP corporativo
        │        └──────────localhost──────────▶ API (uvicorn)          │
        └───────────────────────────────────────────────────────────────┘
              │ VPC (egress a rangos privados)
              ├──▶ Cloud SQL PostgreSQL 16 (solo IP privada, TLS, PITR, regional en production)
              ├──▶ Memorystore for Redis (las sesiones, con AUTH y TLS)
              └──▶ Filestore (el almacén de artefactos, NFS)

        Cloud Run "worker" (CPU siempre asignada, sin entrada) ──▶ Cloud SQL, Filestore, OSRM
        Compute Engine "osrm" (sin IP pública, la red vial en un disco) ◀── solo el worker (5000)
        Cloud Run jobs: migraciones (antes de cada revisión), respaldo (cada noche, Cloud Scheduler)
        GCS: el bucket de respaldos (sin acceso público, versionado, retención mínima de 30 días)
        Artifact Registry: las imágenes, con etiquetas inmutables, desplegadas por digest
        Secret Manager: un secreto por credencial, leído solo por su cuenta de servicio
        Cloud Monitoring: uptime check, 5xx del balanceador, respaldo fallido, CPU de Cloud SQL
        Workload Identity Federation: el despliegue desde GitHub Actions, sin llaves
```

**Un servicio con tres contenedores.** El edge, oauth2-proxy y la API corren en la misma instancia de
Cloud Run (el edge es el contenedor de entrada; los otros dos, sidecars en `localhost`). La API no
tiene ninguna entrada propia, ni siquiera interna, y el edge no tiene que llegar a ella por HTTPS
entre servicios. Escalan juntos: cada instancia atiende la mezcla de la app ([carga.md](carga.md)).

## Del compose a Google Cloud

| Compose de producción | Google Cloud |
| --- | --- |
| `edge` con TLS en 8443 | el balanceador termina TLS (certificado administrado, `MODERN`, TLS 1.2+); el edge sirve http en 8080 detrás de él |
| `oauth2-proxy` + `valkey` | sidecar + Memorystore for Redis (AUTH y TLS) |
| `dex` | el IdP corporativo (fuera de esta infraestructura) |
| `api` | sidecar del servicio `web` |
| `worker` | servicio de Cloud Run con CPU siempre asignada, de 1 a 3 instancias |
| `migraciones` | job de Cloud Run que el despliegue corre antes de cada revisión |
| `postgres` | Cloud SQL PostgreSQL 16 con PostGIS |
| volumen `fuentes` | Filestore (NFS), montado en `web`, `worker` y el respaldo |
| `osrm` | VM de Compute Engine (Container-Optimized OS) con la red en un disco |
| `respaldo` | job de Cloud Run + Cloud Scheduler, escribe en GCS |
| Docker secrets | Secret Manager, montado como archivos (uno por volumen) |
| redes internas | una VPC sin NAT; solo el balanceador es público |
| perfil `observabilidad` | Cloud Logging, Cloud Monitoring (y Cloud Trace, ver abajo) |

## Identidades y permisos

Una cuenta de servicio por función, cada una con lo mínimo, y ninguna con `roles/editor` ni
`roles/owner` (la política lo revisa):

| Cuenta | Qué puede |
| --- | --- |
| `web` | conectarse a Cloud SQL, leer sus cinco secretos y las imágenes, escribir logs, métricas y trazas |
| `worker` | lo mismo, con su secreto |
| `migraciones` | conectarse a Cloud SQL y leer su secreto (el DSN del dueño del esquema) |
| `respaldo` | conectarse a Cloud SQL, leer su secreto, escribir en el bucket de respaldos |
| `osrm` | leer su imagen |
| `programador` | correr el job de respaldo (`run.invoker` sobre ese job) |
| `despliegue` | subir imágenes al repositorio, desplegar revisiones (`run.developer`) y actuar como `web`, `migraciones` y `respaldo` (no como todas las del proyecto) |

**Sin llaves de cuentas de servicio.** El despliegue entra por Workload Identity Federation: GitHub
Actions presenta su token OIDC y el proveedor solo lo acepta de este repositorio y del environment de
GitHub del mismo nombre que el entorno (que exige aprobación).

**Ningún secreto en el estado.** OpenTofu crea el contenedor de cada secreto y su acceso; el valor
(cada versión) se crea con `gcloud`, fuera ([runbooks/secret-rotation.md](runbooks/secret-rotation.md)).
Lo único sensible en el estado es la cadena de AUTH de Memorystore, que genera Google: el bucket del
estado es privado, con versionado, y solo lo lee la identidad de despliegue.

## Red y datos

- **Una VPC propia**, una subred con acceso privado a las APIs de Google y el peering de servicios
  privados para Cloud SQL, Memorystore y Filestore. Sin Cloud NAT: nada sale a Internet. Por omisión
  no entra nada; se abren solo el worker hacia OSRM (5000) y SSH a la VM de OSRM desde IAP.
- **Cloud SQL**: solo IP privada, TLS obligatorio, respaldos automáticos diarios (30) y PITR con 7
  días de bitácora, protegida contra el borrado en OpenTofu y en la API, regional (alta
  disponibilidad) en production. Los roles de la aplicación se crean como en
  `infra/postgres/20-roles.sh`.
- **El almacén en Filestore**: la aplicación necesita un sistema de archivos compartido con renombrado
  atómico y `fsync`. Un almacén nativo de objetos (GCS) es trabajo de v1.0: la interfaz
  `SourceArtifactStore` ya lo permite.
- **Los respaldos** ([backups.md](backups.md)): además de los de Cloud SQL, el job nocturno de
  `scripts/respaldo.py` lleva la base y el almacén juntos al bucket, verificables y restaurables en
  cualquier PostgreSQL 16.

## Observabilidad en la nube

- **Logs**: la salida JSON de cada contenedor llega a Cloud Logging. Los campos de motor-cartera
  (`level`, `request_id`, `trace_id`...) quedan en el `jsonPayload`; Cloud Logging no reconoce
  `level` como su `severity`, y mapearlo (o agregar `severity` y `logging.googleapis.com/trace` al
  formato) es parte de lo que falta.
- **Métricas**: las de la plataforma (el balanceador, Cloud Run, Cloud SQL) en Cloud Monitoring, con
  cuatro alertas de umbral inicial (`infra/gcp/monitoreo_y_wif.tf`). Las de la aplicación (`mc_*`)
  necesitan un sidecar de Managed Service for Prometheus.
- **Trazas**: la aplicación exporta OTLP/HTTP; en Cloud Run, un colector como sidecar que las envíe
  a Cloud Trace. No está en la referencia todavía.

## El despliegue

`.github/workflows/deploy.yml`, **que no puede correr en este repositorio**: solo a mano
(`workflow_dispatch`), solo desde `main` o una etiqueta `v*`, con una confirmación escrita
(`desplegar <entorno>`), con el environment de GitHub del entorno (que exige la aprobación de un
revisor) y solo si ese environment declara `GCP_PROYECTO`, `GCP_REGION`, `GCP_WIF_PROVIDER`,
`GCP_SERVICE_ACCOUNT` y `MC_DOMINIO`. No aplica la infraestructura: construye las imágenes, las sube
con el SHA del commit como etiqueta inmutable, corre el job de migraciones con la imagen nueva por su
digest, despliega las revisiones nuevas y espera a que la app responda.

## Cómo se valida

```bash
cd infra/gcp
tofu fmt -check -recursive
tofu init -backend=false -lockfile=readonly
tofu validate
python ../../scripts/verificar_iac.py
```

El CI corre además el plan de `entornos/staging.tfvars` y `entornos/production.tfvars` en una copia
con un backend local y un token falso, sin estado: los dos planean 95 recursos para agregar, ninguno
para cambiar ni destruir (2026-10-18, proveedor `hashicorp/google` 8.7.0 en el lock). La política
revisa 36 reglas: ningún rol amplio ni miembro público, Cloud SQL privada y protegida con PITR, el
bucket privado y versionado, etiquetas inmutables, imágenes por digest, ningún servicio abierto a
todo Internet, ninguna VM con IP pública, ningún secreto en el código, WIF atado al repositorio, TLS
moderno y el estado remoto.

## Lo que falta para un despliegue real

1. **Un proyecto por entorno**, con facturación, cuotas y las APIs habilitadas, y el bucket del
   estado (versionado, privado).
2. **El dominio** y su registro A hacia la IP del balanceador; el certificado administrado se emite
   cuando el dominio resuelve.
3. **El IdP corporativo**: el cliente de oauth2-proxy (con su secreto en `oauth2_proxy_cfg`), los
   grupos `mc-*` y sus miembros, y su emisor y audiencia en las variables.
4. **La plantilla del edge detrás del balanceador**: http en 8080 sin redirigir (TLS termina en el
   balanceador), la IP real desde el balanceador para los límites (o los límites solo en Cloud Armor),
   HSTS, y sin la location del IdP local. La imagen de hoy trae las de desarrollo y de producción
   local, no esa.
5. **Los roles de la base y las versiones de cada secreto**, creados fuera de OpenTofu.
6. **La red vial de OSRM** preparada en su disco ([red_vial.md](red_vial.md)).
7. **La observabilidad de la aplicación**: el colector de trazas y el de métricas como sidecars, y el
   mapeo de `severity` en los logs.
8. **Aplicarla**, por una persona con permisos para hacerlo, revisando el plan.

## Costo aproximado

Sin precios exactos (cambian, y dependen de la región y del uso): lo que más pesa es Cloud SQL
(regional en production duplica su costo), Filestore (el nivel básico empieza en 1 TiB), Memorystore
(con alta disponibilidad en production), el balanceador y Cloud Armor, y las instancias mínimas de
Cloud Run (una del servicio web en production, una del worker siempre). Staging escala a cero el
servicio web y usa una base zonal pequeña.

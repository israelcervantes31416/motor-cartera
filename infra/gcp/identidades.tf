# Una cuenta de servicio por funcion, cada una con lo minimo. Ninguna tiene roles/editor ni
# roles/owner (scripts/verificar_iac.py lo revisa): cada permiso se da sobre el recurso o, si no hay
# forma, con un rol predefinido estrecho sobre el proyecto.

resource "google_service_account" "web" {
  account_id   = "${local.prefijo}-web"
  display_name = "motor-cartera web: edge, oauth2-proxy y la API (Cloud Run)"
}

resource "google_service_account" "worker" {
  account_id   = "${local.prefijo}-worker"
  display_name = "motor-cartera worker (Compute Engine)"
}

resource "google_service_account" "osrm" {
  account_id   = "${local.prefijo}-osrm"
  display_name = "motor-cartera OSRM (Compute Engine)"
}

resource "google_service_account" "migraciones" {
  account_id   = "${local.prefijo}-migraciones"
  display_name = "motor-cartera migraciones (Cloud Run job)"
}

resource "google_service_account" "respaldo" {
  account_id   = "${local.prefijo}-respaldo"
  display_name = "motor-cartera respaldo (Cloud Run job)"
}

resource "google_service_account" "programador" {
  account_id   = "${local.prefijo}-programador"
  display_name = "motor-cartera Cloud Scheduler: dispara el respaldo"
}

resource "google_service_account" "despliegue" {
  account_id   = "${local.prefijo}-despliegue"
  display_name = "motor-cartera despliegue desde GitHub Actions (WIF)"
}

locals {
  cuentas = {
    web         = google_service_account.web.email
    worker      = google_service_account.worker.email
    osrm        = google_service_account.osrm.email
    migraciones = google_service_account.migraciones.email
    respaldo    = google_service_account.respaldo.email
  }

  # Escribir sus logs, sus metricas y sus trazas: todas las que corren codigo.
  telemetria = {
    for par in setproduct(keys(local.cuentas), [
      "roles/logging.logWriter",
      "roles/monitoring.metricWriter",
      "roles/cloudtrace.agent",
    ]) : "${par[0]}-${par[1]}" => { cuenta = par[0], rol = par[1] }
  }

  # Conectarse a Cloud SQL: las que usan la base.
  base = toset(["web", "worker", "migraciones", "respaldo"])
}

resource "google_project_iam_member" "telemetria" {
  for_each = local.telemetria
  project  = var.proyecto
  role     = each.value.rol
  member   = "serviceAccount:${local.cuentas[each.value.cuenta]}"
}

resource "google_project_iam_member" "cliente_sql" {
  for_each = local.base
  project  = var.proyecto
  role     = "roles/cloudsql.client"
  member   = "serviceAccount:${local.cuentas[each.key]}"
}

# Leer las imagenes del repositorio: las que las ejecutan.
resource "google_artifact_registry_repository_iam_member" "lectores" {
  for_each   = local.cuentas
  location   = google_artifact_registry_repository.imagenes.location
  repository = google_artifact_registry_repository.imagenes.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${each.value}"
}

# El respaldo escribe en su bucket y lee lo que escribio; nadie mas toca el bucket.
resource "google_storage_bucket_iam_member" "respaldo_escribe" {
  bucket = google_storage_bucket.respaldos.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.respaldo.email}"
}

# El despliegue: subir imagenes y desplegar revisiones de Cloud Run con las cuentas de servicio de
# ejecucion (actAs sobre cada una, no sobre todas las del proyecto).
resource "google_artifact_registry_repository_iam_member" "despliegue_escribe" {
  location   = google_artifact_registry_repository.imagenes.location
  repository = google_artifact_registry_repository.imagenes.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.despliegue.email}"
}

resource "google_project_iam_member" "despliegue_run" {
  project = var.proyecto
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.despliegue.email}"
}

resource "google_service_account_iam_member" "despliegue_actua_como" {
  for_each           = toset(["web", "migraciones", "respaldo"])
  service_account_id = "projects/${var.proyecto}/serviceAccounts/${local.cuentas[each.key]}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.despliegue.email}"
}

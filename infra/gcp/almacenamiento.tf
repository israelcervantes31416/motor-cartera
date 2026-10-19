# El almacen de artefactos (MC_SOURCE_STORE_ROOT): cada archivo recibido, por su SHA-256. La API
# lo escribe y el worker lo lee, asi que es un sistema de archivos compartido con renombrado atomico
# y fsync: Filestore (NFS), montado en el servicio web de Cloud Run y en el worker. Un almacen nativo
# de objetos (GCS) es trabajo de v1.0: la interfaz SourceArtifactStore ya lo permite.
resource "google_filestore_instance" "fuentes" {
  name     = "${local.prefijo}-fuentes"
  location = var.zona
  tier     = "BASIC_HDD"

  file_shares {
    name        = "fuentes"
    capacity_gb = 1024
  }

  networks {
    network      = google_compute_network.principal.name
    modes        = ["MODE_IPV4"]
    connect_mode = "PRIVATE_SERVICE_ACCESS"
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_service_networking_connection.servicios_privados]
}

# Los respaldos de scripts/respaldo.py (la base y el almacen, con su manifiesto): un bucket sin
# acceso publico posible, con versionado, retencion minima de 30 dias (un respaldo no se borra antes,
# ni por error) y que pasa a Nearline despues de 30 y se borra despues de 180. La retencion no se
# bloquea aqui: bloquearla es irreversible y lo decide quien opera el entorno.
resource "google_storage_bucket" "respaldos" {
  name                        = "${var.proyecto}-${local.prefijo}-respaldos"
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  retention_policy {
    retention_period = 2592000
    is_locked        = false
  }

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  lifecycle_rule {
    condition {
      age = 180
    }
    action {
      type = "Delete"
    }
  }
}

# Las imagenes: un repositorio de Docker con etiquetas inmutables (una etiqueta no se reasigna) y
# el escaneo de vulnerabilidades de Artifact Analysis. El despliegue usa siempre el digest.
resource "google_artifact_registry_repository" "imagenes" {
  location      = var.region
  repository_id = "motor-cartera"
  format        = "DOCKER"
  description   = "Las imagenes de motor-cartera, por digest."

  docker_config {
    immutable_tags = true
  }
}

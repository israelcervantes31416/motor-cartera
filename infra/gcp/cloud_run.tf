# El servicio web: un solo servicio de Cloud Run con tres contenedores en la misma instancia. El
# edge (nginx con la SPA) es el contenedor de entrada; oauth2-proxy y la API son sidecars en
# localhost, asi que la API no tiene ninguna entrada propia, ni siquiera interna. Solo el balanceador
# llega al servicio (ingress INTERNAL_LOAD_BALANCER), y TLS termina en el balanceador.
#
# Antes del primer despliegue real hace falta la plantilla del edge para correr detras del
# balanceador (http en 8080, la IP real desde el balanceador, los limites por IP en Cloud Armor y sin
# la location del IdP local): docs/cloud-gcp.md, "Lo que falta para un despliegue real".

locals {
  # Cada secreto se monta en su directorio (Cloud Run monta uno por volumen) y la aplicacion lo lee
  # por su variable *_FILE.
  volumenes_web = ["dsn_api", "api_claves", "gateway_secretos", "auditoria_clave", "oauth2_proxy_cfg"]
}

resource "google_cloud_run_v2_service" "web" {
  name                 = "${local.prefijo}-web"
  location             = var.region
  ingress              = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection  = local.produccion
  invoker_iam_disabled = true

  template {
    service_account                  = google_service_account.web.email
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
    max_instance_request_concurrency = 40
    timeout                          = "60s"

    scaling {
      min_instance_count = var.api_instancias_minimas
      max_instance_count = var.api_instancias_maximas
    }

    vpc_access {
      network_interfaces {
        network    = google_compute_network.principal.id
        subnetwork = google_compute_subnetwork.principal.id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    containers {
      name       = "edge"
      image      = var.imagen_web
      depends_on = ["api", "oauth2-proxy"]

      ports {
        container_port = 8080
      }

      resources {
        limits = { cpu = "1", memory = "256Mi" }
      }

      env {
        name  = "NGINX_ENVSUBST_TEMPLATE_DIR"
        value = "/etc/nginx/plantillas/balanceador"
      }
      env {
        name  = "MC_EDGE_HOST"
        value = var.dominio
      }
      env {
        name  = "MC_API_UPSTREAM"
        value = "127.0.0.1:8000"
      }
      env {
        name  = "MC_OAUTH2_PROXY_UPSTREAM"
        value = "127.0.0.1:4180"
      }
      env {
        name  = "MC_GATEWAY_SECRETS_FILE"
        value = "/run/secrets/gateway_secretos/valor"
      }

      volume_mounts {
        name       = "gateway-secretos"
        mount_path = "/run/secrets/gateway_secretos"
      }

      startup_probe {
        http_get {
          path = "/salud-web"
          port = 8080
        }
        period_seconds    = 2
        failure_threshold = 15
      }
    }

    containers {
      name  = "oauth2-proxy"
      image = var.imagen_oauth2_proxy
      args = [
        "--config=/run/secrets/oauth2_proxy_cfg/valor",
        "--http-address=127.0.0.1:4180",
        "--provider=oidc",
        "--oidc-issuer-url=${var.oidc_emisor}",
        "--code-challenge-method=S256",
        "--insecure-oidc-skip-nonce=false",
        "--scope=openid email profile groups offline_access",
        "--redirect-url=https://${var.dominio}/oauth2/callback",
        "--whitelist-domain=${var.dominio}",
        "--email-domain=*",
        "--upstream=static://202",
        "--session-store-type=redis",
        "--cookie-name=__Host-mc_sesion",
        "--cookie-secure=true",
        "--cookie-httponly=true",
        "--cookie-samesite=lax",
        "--cookie-expire=8h",
        "--cookie-refresh=5m",
        "--cookie-csrf-per-request=true",
        "--set-xauthrequest=true",
        "--pass-access-token=true",
        "--skip-provider-button=true",
        "--reverse-proxy=true",
        "--trusted-proxy-ip=127.0.0.1/32",
        "--show-debug-on-error=false",
        "--auth-logging=false",
      ]

      resources {
        limits = { cpu = "0.5", memory = "128Mi" }
      }

      volume_mounts {
        name       = "oauth2-proxy-cfg"
        mount_path = "/run/secrets/oauth2_proxy_cfg"
      }

      startup_probe {
        http_get {
          path = "/ping"
          port = 4180
        }
        period_seconds    = 2
        failure_threshold = 15
      }
    }

    containers {
      name  = "api"
      image = var.imagen_api
      args = [
        "uvicorn", "--factory", "motor_cartera.api.app:crear_app", "--host", "127.0.0.1",
        "--port", "8000", "--no-access-log", "--no-server-header", "--no-proxy-headers",
        "--timeout-graceful-shutdown", "8",
      ]

      resources {
        limits = { cpu = "1", memory = "1Gi" }
      }

      dynamic "env" {
        for_each = {
          MC_APP_ENV              = "production"
          MC_LOG_FORMATO          = "json"
          MC_SOURCE_STORE_ROOT    = "/var/lib/motor-cartera/fuentes"
          MC_DATABASE_URL_FILE    = "/run/secrets/dsn_api/valor"
          MC_API_KEYS_FILE        = "/run/secrets/api_claves/valor"
          MC_GATEWAY_SECRETS_FILE = "/run/secrets/gateway_secretos/valor"
          MC_AUDITORIA_CLAVE_FILE = "/run/secrets/auditoria_clave/valor"
          MC_OIDC_ISSUER          = var.oidc_emisor
          MC_OIDC_AUDIENCE        = var.oidc_audiencia
          MC_OIDC_GRUPOS          = local.grupos
        }
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "volume_mounts" {
        for_each = toset(["dsn_api", "api_claves", "gateway_secretos", "auditoria_clave"])
        content {
          name       = replace(volume_mounts.value, "_", "-")
          mount_path = "/run/secrets/${volume_mounts.value}"
        }
      }

      volume_mounts {
        name       = "fuentes"
        mount_path = "/var/lib/motor-cartera/fuentes"
      }

      startup_probe {
        http_get {
          path = "/health/ready"
          port = 8000
        }
        period_seconds    = 3
        failure_threshold = 20
      }

      liveness_probe {
        http_get {
          path = "/health/live"
          port = 8000
        }
        period_seconds = 15
      }
    }

    dynamic "volumes" {
      for_each = toset(local.volumenes_web)
      content {
        name = replace(volumes.value, "_", "-")
        secret {
          secret = google_secret_manager_secret.secretos[volumes.value].secret_id
          items {
            version = "latest"
            path    = "valor"
          }
        }
      }
    }

    volumes {
      name = "fuentes"
      nfs {
        server    = google_filestore_instance.fuentes.networks[0].ip_addresses[0]
        path      = "/fuentes"
        read_only = false
      }
    }
  }

  depends_on = [google_secret_manager_secret_iam_member.lectores]
}

# El worker: tambien en Cloud Run, con CPU siempre asignada (no escala a cero ni se congela entre
# peticiones) y sin entrada: su puerto es el de su salud y sus metricas. Toma trabajos de la cola en
# PostgreSQL; varias instancias nunca toman el mismo (FOR UPDATE SKIP LOCKED).
resource "google_cloud_run_v2_service" "worker" {
  name                = "${local.prefijo}-worker"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = local.produccion

  template {
    service_account       = google_service_account.worker.email
    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"

    scaling {
      min_instance_count = 1
      max_instance_count = local.produccion ? 3 : 1
    }

    vpc_access {
      network_interfaces {
        network    = google_compute_network.principal.id
        subnetwork = google_compute_subnetwork.principal.id
        tags       = ["mc-worker"]
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = var.imagen_api
      args  = ["motor-cartera", "worker"]

      ports {
        container_port = 9465
      }

      resources {
        limits   = { cpu = "2", memory = "2Gi" }
        cpu_idle = false
      }

      dynamic "env" {
        for_each = {
          MC_APP_ENV           = "production"
          MC_LOG_FORMATO       = "json"
          MC_METRICAS_PUERTO   = "9465"
          MC_SOURCE_STORE_ROOT = "/var/lib/motor-cartera/fuentes"
          MC_DATABASE_URL_FILE = "/run/secrets/dsn_worker/valor"
          MC_OSRM_URL          = "http://${google_compute_address.osrm.address}:5000"
        }
        content {
          name  = env.key
          value = env.value
        }
      }

      volume_mounts {
        name       = "dsn-worker"
        mount_path = "/run/secrets/dsn_worker"
      }

      volume_mounts {
        name       = "fuentes"
        mount_path = "/var/lib/motor-cartera/fuentes"
      }

      liveness_probe {
        http_get {
          path = "/health/live"
          port = 9465
        }
        period_seconds = 30
      }
    }

    volumes {
      name = "dsn-worker"
      secret {
        secret = google_secret_manager_secret.secretos["dsn_worker"].secret_id
        items {
          version = "latest"
          path    = "valor"
          mode    = 288
        }
      }
    }

    volumes {
      name = "fuentes"
      nfs {
        server    = google_filestore_instance.fuentes.networks[0].ip_addresses[0]
        path      = "/fuentes"
        read_only = false
      }
    }
  }

  depends_on = [google_secret_manager_secret_iam_member.lectores]
}

# Las migraciones: un job que el despliegue corre antes de cada revision nueva (alembic upgrade
# head, con el candado de PostgreSQL contra dos a la vez). Con el rol dueno del esquema.
resource "google_cloud_run_v2_job" "migraciones" {
  name                = "${local.prefijo}-migraciones"
  location            = var.region
  deletion_protection = local.produccion

  template {
    task_count = 1

    template {
      service_account = google_service_account.migraciones.email
      max_retries     = 0
      timeout         = "900s"

      vpc_access {
        network_interfaces {
          network    = google_compute_network.principal.id
          subnetwork = google_compute_subnetwork.principal.id
        }
        egress = "PRIVATE_RANGES_ONLY"
      }

      containers {
        image = var.imagen_api
        args  = ["alembic", "upgrade", "head"]

        env {
          name  = "MC_APP_ENV"
          value = "production"
        }
        env {
          name  = "MC_DATABASE_URL_FILE"
          value = "/run/secrets/dsn_migraciones/valor"
        }

        volume_mounts {
          name       = "dsn-migraciones"
          mount_path = "/run/secrets/dsn_migraciones"
        }
      }

      volumes {
        name = "dsn-migraciones"
        secret {
          secret = google_secret_manager_secret.secretos["dsn_migraciones"].secret_id
          items {
            version = "latest"
            path    = "valor"
          }
        }
      }
    }
  }

  depends_on = [google_secret_manager_secret_iam_member.lectores]
}

# El respaldo oficial (scripts/respaldo.py): la base con el rol que solo lee y el almacen montado de
# solo lectura, escrito en el bucket de respaldos montado como sistema de archivos. Ademas de los
# respaldos automaticos y el PITR de Cloud SQL: este es el que lleva el almacen y se restaura en
# cualquier PostgreSQL 16 (docs/backups.md).
resource "google_cloud_run_v2_job" "respaldo" {
  name                = "${local.prefijo}-respaldo"
  location            = var.region
  deletion_protection = local.produccion

  template {
    task_count = 1

    template {
      service_account       = google_service_account.respaldo.email
      execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
      max_retries           = 1
      timeout               = "3600s"

      vpc_access {
        network_interfaces {
          network    = google_compute_network.principal.id
          subnetwork = google_compute_subnetwork.principal.id
        }
        egress = "PRIVATE_RANGES_ONLY"
      }

      containers {
        image = var.imagen_respaldo
        args  = ["respaldar", "--destino", "/respaldos"]

        env {
          name  = "MC_RESPALDO_DSN_FILE"
          value = "/run/secrets/dsn_respaldo/valor"
        }
        env {
          name  = "MC_SOURCE_STORE_ROOT"
          value = "/var/lib/motor-cartera/fuentes"
        }

        volume_mounts {
          name       = "dsn-respaldo"
          mount_path = "/run/secrets/dsn_respaldo"
        }
        volume_mounts {
          name       = "fuentes"
          mount_path = "/var/lib/motor-cartera/fuentes"
        }
        volume_mounts {
          name       = "respaldos"
          mount_path = "/respaldos"
        }
      }

      volumes {
        name = "dsn-respaldo"
        secret {
          secret = google_secret_manager_secret.secretos["dsn_respaldo"].secret_id
          items {
            version = "latest"
            path    = "valor"
          }
        }
      }

      volumes {
        name = "fuentes"
        nfs {
          server    = google_filestore_instance.fuentes.networks[0].ip_addresses[0]
          path      = "/fuentes"
          read_only = true
        }
      }

      volumes {
        name = "respaldos"
        gcs {
          bucket    = google_storage_bucket.respaldos.name
          read_only = false
        }
      }
    }
  }

  depends_on = [google_secret_manager_secret_iam_member.lectores]
}

# Cada noche, a las 03:00 de la Ciudad de Mexico, Cloud Scheduler corre el job de respaldo.
resource "google_cloud_run_v2_job_iam_member" "programador_corre_el_respaldo" {
  name     = google_cloud_run_v2_job.respaldo.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.programador.email}"
}

resource "google_cloud_scheduler_job" "respaldo_diario" {
  name      = "${local.prefijo}-respaldo-diario"
  region    = var.region
  schedule  = "0 3 * * *"
  time_zone = "America/Mexico_City"

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.proyecto}/locations/${var.region}/jobs/${google_cloud_run_v2_job.respaldo.name}:run"

    oauth_token {
      service_account_email = google_service_account.programador.email
    }
  }
}

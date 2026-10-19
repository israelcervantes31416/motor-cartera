# PostgreSQL 16 en Cloud SQL, solo con IP privada, con respaldos automaticos diarios y
# recuperacion a un punto en el tiempo (PITR, 7 dias de bitacora), protegida contra el borrado en
# OpenTofu y en la API. Regional (alta disponibilidad) en production. PostGIS se crea en la base
# con su extension (la migracion 0011 lo pide).
#
# Los roles de la aplicacion (mc_migraciones, dueno; mc_api y mc_worker, DML; mc_respaldo, solo
# lectura) y sus contrasenas se crean fuera de OpenTofu, igual que en infra/postgres/20-roles.sh, y
# sus DSN se guardan como versiones de Secret Manager: ninguna contrasena pasa por el estado.

resource "google_sql_database_instance" "principal" {
  name                = "${local.prefijo}-pg"
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = true

  settings {
    tier                        = var.sql_tier
    edition                     = "ENTERPRISE"
    availability_type           = local.produccion ? "REGIONAL" : "ZONAL"
    disk_type                   = "PD_SSD"
    disk_autoresize             = true
    deletion_protection_enabled = true
    user_labels                 = local.etiquetas

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = google_compute_network.principal.id
      enable_private_path_for_google_cloud_services = true
      ssl_mode                                      = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "08:00"
      transaction_log_retention_days = 7

      backup_retention_settings {
        retained_backups = 30
        retention_unit   = "COUNT"
      }
    }

    maintenance_window {
      day          = 7
      hour         = 9
      update_track = "stable"
    }

    insights_config {
      query_insights_enabled  = true
      record_application_tags = false
      record_client_address   = false
    }
  }

  depends_on = [google_service_networking_connection.servicios_privados]
}

resource "google_sql_database" "cartera" {
  name     = "cartera"
  instance = google_sql_database_instance.principal.name
}

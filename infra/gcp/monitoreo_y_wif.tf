# Las alertas de Cloud Monitoring. Sus umbrales son INICIALES (como los de
# infra/observabilidad/alertas.yml): se ajustan con la operacion real, no con esta referencia.

locals {
  canales = var.canal_de_alertas == "" ? [] : [var.canal_de_alertas]
}

resource "google_monitoring_uptime_check_config" "edge" {
  display_name = "${local.prefijo} edge /salud-web"
  timeout      = "10s"
  period       = "60s"

  http_check {
    path         = "/salud-web"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.proyecto
      host       = var.dominio
    }
  }
}

resource "google_monitoring_alert_policy" "edge_caido" {
  display_name          = "${local.prefijo} el edge no responde (umbral inicial)"
  combiner              = "OR"
  notification_channels = local.canales

  conditions {
    display_name = "La sonda de /salud-web falla"

    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.edge.uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "300s"

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.project_id"]
      }
    }
  }

  documentation {
    content   = "Runbook: docs/runbooks/service-down.md"
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_alert_policy" "errores_5xx" {
  display_name          = "${local.prefijo} 5xx del balanceador (umbral inicial)"
  combiner              = "OR"
  notification_channels = local.canales

  conditions {
    display_name = "Mas de una respuesta 5xx por segundo durante 5 minutos"

    condition_threshold {
      filter          = "metric.type=\"loadbalancing.googleapis.com/https/request_count\" AND resource.type=\"https_lb_rule\" AND metric.label.response_code_class=\"500\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "300s"

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  documentation {
    content   = "Runbook: docs/runbooks/service-down.md"
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_alert_policy" "respaldo_fallido" {
  display_name          = "${local.prefijo} el respaldo nocturno fallo (umbral inicial)"
  combiner              = "OR"
  notification_channels = local.canales

  conditions {
    display_name = "Una ejecucion fallida del job de respaldo"

    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/job/completed_execution_count\" AND resource.type=\"cloud_run_job\" AND resource.label.job_name=\"${google_cloud_run_v2_job.respaldo.name}\" AND metric.label.result=\"failed\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period   = "3600s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  documentation {
    content   = "Runbook: docs/runbooks/backup-restore.md"
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_alert_policy" "base_saturada" {
  display_name          = "${local.prefijo} Cloud SQL con CPU alta (umbral inicial)"
  combiner              = "OR"
  notification_channels = local.canales

  conditions {
    display_name = "CPU de Cloud SQL sobre 80 % durante 10 minutos"

    condition_threshold {
      filter          = "metric.type=\"cloudsql.googleapis.com/database/cpu/utilization\" AND resource.type=\"cloudsql_database\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.8
      duration        = "600s"

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }

  documentation {
    content   = "Runbook: docs/runbooks/database-down.md"
    mime_type = "text/markdown"
  }
}

# Workload Identity Federation: el workflow de despliegue de GitHub Actions toma la identidad de
# despliegue sin ninguna llave guardada. Solo desde este repositorio y solo desde el environment de
# GitHub del mismo nombre que el entorno (que exige aprobacion).
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "${local.prefijo}-github"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"        = "assertion.sub"
    "attribute.repository"  = "assertion.repository"
    "attribute.environment" = "assertion.environment"
    "attribute.ref"         = "assertion.ref"
  }

  attribute_condition = "assertion.repository == '${var.repositorio_github}' && assertion.environment == '${var.entorno}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "despliegue_desde_github" {
  service_account_id = google_service_account.despliegue.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.repositorio_github}"
}

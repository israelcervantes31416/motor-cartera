output "ip_publica" {
  description = "La IP del balanceador: el registro A de var.dominio apunta aqui."
  value       = google_compute_global_address.publica.address
}

output "servicio_web" {
  description = "El servicio de Cloud Run del edge, oauth2-proxy y la API."
  value       = google_cloud_run_v2_service.web.name
}

output "servicio_worker" {
  value = google_cloud_run_v2_service.worker.name
}

output "job_migraciones" {
  value = google_cloud_run_v2_job.migraciones.name
}

output "job_respaldo" {
  value = google_cloud_run_v2_job.respaldo.name
}

output "bucket_respaldos" {
  value = google_storage_bucket.respaldos.name
}

output "repositorio_de_imagenes" {
  value = "${var.region}-docker.pkg.dev/${var.proyecto}/${google_artifact_registry_repository.imagenes.repository_id}"
}

output "proveedor_wif" {
  description = "GCP_WIF_PROVIDER del environment de GitHub (.github/workflows/deploy.yml)."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "cuenta_de_despliegue" {
  description = "GCP_SERVICE_ACCOUNT del environment de GitHub."
  value       = google_service_account.despliegue.email
}

output "redis_sesiones" {
  description = "El host de Memorystore para la configuracion de oauth2-proxy (su AUTH se lee con gcloud, no sale aqui)."
  value       = google_redis_instance.sesiones.host
}

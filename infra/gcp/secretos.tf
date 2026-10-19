# Los secretos de cada funcion. OpenTofu crea el contenedor y le da acceso de lectura solo a la
# cuenta de servicio que lo usa; el valor (cada version) se crea fuera, con gcloud, y se rota sin
# tocar esta infraestructura (docs/runbooks/secret-rotation.md). Ningun valor pasa por el estado.

resource "google_secret_manager_secret" "secretos" {
  for_each            = local.secretos
  secret_id           = "${local.prefijo}-${replace(each.key, "_", "-")}"
  deletion_protection = local.produccion

  replication {
    auto {}
  }
}

locals {
  lectores_de_secretos = merge([
    for secreto, funciones in local.secretos : {
      for funcion in funciones : "${secreto}-${funcion}" => { secreto = secreto, funcion = funcion }
    }
  ]...)
}

resource "google_secret_manager_secret_iam_member" "lectores" {
  for_each  = local.lectores_de_secretos
  secret_id = google_secret_manager_secret.secretos[each.value.secreto].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.cuentas[each.value.funcion]}"
}

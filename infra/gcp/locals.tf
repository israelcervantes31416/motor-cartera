locals {
  prefijo    = "mc-${var.entorno}"
  produccion = var.entorno == "production"

  etiquetas = {
    app     = "motor-cartera"
    entorno = var.entorno
    gestion = "opentofu"
  }

  # Los secretos: Secret Manager guarda sus versiones, que se crean fuera de OpenTofu (runbook
  # secret-rotation). Aqui solo existe el contenedor de cada uno y quien lo puede leer: ningun valor
  # pasa por el estado.
  secretos = {
    dsn_api          = ["web"]
    dsn_worker       = ["worker"]
    dsn_migraciones  = ["migraciones"]
    dsn_respaldo     = ["respaldo"]
    api_claves       = ["web"]
    gateway_secretos = ["web"]
    auditoria_clave  = ["web"]
    oauth2_proxy_cfg = ["web"]
  }

  # Los grupos del IdP corporativo con que la API arma los permisos (MC_OIDC_GRUPOS).
  grupos = "mc-lectores=VIEWER;mc-auditores=AUDITOR;mc-operadores=OPERATOR;mc-administradores=ADMIN"
}

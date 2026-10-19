# La arquitectura de referencia de motor-cartera en Google Cloud (docs/cloud-gcp.md), en OpenTofu.
#
# ES UNA REFERENCIA: se valida (fmt, init -backend=false, validate, el plan sin credenciales y
# scripts/verificar_iac.py) y NUNCA se aplica desde este repositorio. Aplicarla crea recursos con
# costo en un proyecto real; eso es una decision humana, con su proyecto, su dominio, su IdP y sus
# credenciales, fuera de esta version.
#
# El estado vive en un bucket de GCS con versionado (backend "gcs"); su bucket y su prefijo se dan
# al inicializar, nunca aqui:
#   tofu init -backend-config="bucket=<bucket-del-estado>" -backend-config="prefix=motor-cartera/<entorno>"

terraform {
  required_version = ">= 1.8.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.7"
    }
  }

  backend "gcs" {}
}

provider "google" {
  project = var.proyecto
  region  = var.region

  default_labels = local.etiquetas
}

variable "proyecto" {
  description = "El proyecto de Google Cloud del entorno (uno por entorno: staging y production no comparten proyecto)."
  type        = string
}

variable "entorno" {
  description = "staging o production. Production es regional en la base y no escala a cero."
  type        = string

  validation {
    condition     = contains(["staging", "production"], var.entorno)
    error_message = "El entorno es staging o production."
  }
}

variable "region" {
  description = "La region de todo el entorno (northamerica-south1 es Queretaro)."
  type        = string
  default     = "northamerica-south1"
}

variable "zona" {
  description = "La zona de lo que es zonal: el almacen de artefactos (Filestore) y la VM de OSRM."
  type        = string
  default     = "northamerica-south1-a"
}

variable "dominio" {
  description = "El dominio publico de la app (su certificado lo administra Google)."
  type        = string
}

variable "imagen_api" {
  description = "La imagen de la API y del worker en Artifact Registry, por su digest."
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.imagen_api))
    error_message = "La imagen va fijada por su digest (@sha256:...), nunca por una etiqueta."
  }
}

variable "imagen_web" {
  description = "La imagen del edge (nginx con la SPA) en Artifact Registry, por su digest."
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.imagen_web))
    error_message = "La imagen va fijada por su digest (@sha256:...), nunca por una etiqueta."
  }
}

variable "imagen_respaldo" {
  description = "La imagen de la herramienta de respaldo (infra/respaldo/Dockerfile), por su digest."
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.imagen_respaldo))
    error_message = "La imagen va fijada por su digest (@sha256:...), nunca por una etiqueta."
  }
}

variable "imagen_oauth2_proxy" {
  description = "oauth2-proxy, copiado a Artifact Registry, por su digest."
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.imagen_oauth2_proxy))
    error_message = "La imagen va fijada por su digest (@sha256:...), nunca por una etiqueta."
  }
}

variable "imagen_osrm" {
  description = "OSRM v26.9.0, copiado a Artifact Registry, por su digest."
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.imagen_osrm))
    error_message = "La imagen va fijada por su digest (@sha256:...), nunca por una etiqueta."
  }
}

variable "oidc_emisor" {
  description = "El issuer del IdP corporativo (https). Sus usuarios y grupos no los administra esta infraestructura."
  type        = string

  validation {
    condition     = startswith(var.oidc_emisor, "https://")
    error_message = "El emisor OIDC es https."
  }
}

variable "oidc_audiencia" {
  description = "La audiencia que la API exige en el token (aud)."
  type        = string
}

variable "repositorio_github" {
  description = "propietario/repositorio cuyo workflow de despliegue puede tomar la identidad de despliegue (Workload Identity Federation)."
  type        = string
  default     = "israelcervantes31416/motor-cartera"
}

variable "sql_tier" {
  description = "La maquina de Cloud SQL."
  type        = string
  default     = "db-custom-2-7680"
}

variable "api_instancias_minimas" {
  description = "Instancias siempre encendidas del servicio web (0 en staging: escala a cero)."
  type        = number
  default     = 1
}

variable "api_instancias_maximas" {
  description = "El tope de instancias del servicio web. En la prueba registrada una API atendio 25 lecturas por segundo de la mezcla de la app con p95 de 121 ms (docs/carga.md)."
  type        = number
  default     = 6
}

variable "canal_de_alertas" {
  description = "El canal de notificacion de Cloud Monitoring (su id), si ya existe. Vacio: las alertas quedan sin canal."
  type        = string
  default     = ""
}

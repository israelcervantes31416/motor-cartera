# La unica entrada publica: un balanceador HTTPS externo global con un certificado administrado por
# Google para var.dominio, una politica de TLS moderna (TLS 1.2 o mas) y Cloud Armor delante. Cloud
# Armor limita las peticiones por IP del cliente (la real: el balanceador la ve) y aplica las reglas
# preconfiguradas de OWASP en modo de vista previa (se registran sin bloquear hasta revisarlas). El
# puerto 80 solo redirige a https.

resource "google_compute_global_address" "publica" {
  name = "${local.prefijo}-ip-publica"
}

resource "google_compute_region_network_endpoint_group" "web" {
  name                  = "${local.prefijo}-web"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.web.name
  }
}

resource "google_compute_security_policy" "edge" {
  name        = "${local.prefijo}-edge"
  description = "Limite por IP y reglas OWASP (vista previa) delante del servicio web."

  rule {
    action      = "throttle"
    priority    = 1000
    description = "Lecturas por IP del cliente: 1800 por minuto (30/s, como el edge local)."

    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }

    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"

      rate_limit_threshold {
        count        = 1800
        interval_sec = 60
      }
    }
  }

  rule {
    action      = "deny(403)"
    priority    = 2000
    preview     = true
    description = "Inyeccion SQL (OWASP CRS), en vista previa."

    match {
      expr {
        expression = "evaluatePreconfiguredWaf('sqli-v33-stable', {'sensitivity': 1})"
      }
    }
  }

  rule {
    action      = "deny(403)"
    priority    = 2001
    preview     = true
    description = "XSS (OWASP CRS), en vista previa."

    match {
      expr {
        expression = "evaluatePreconfiguredWaf('xss-v33-stable', {'sensitivity': 1})"
      }
    }
  }

  rule {
    action      = "allow"
    priority    = 2147483647
    description = "Lo demas pasa (y el edge aplica sus propias reglas)."

    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }
}

resource "google_compute_backend_service" "web" {
  name                  = "${local.prefijo}-web"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  security_policy       = google_compute_security_policy.edge.id

  backend {
    group = google_compute_region_network_endpoint_group.web.id
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

resource "google_compute_url_map" "web" {
  name            = "${local.prefijo}-web"
  default_service = google_compute_backend_service.web.id
}

resource "google_compute_managed_ssl_certificate" "web" {
  name = "${local.prefijo}-web"

  managed {
    domains = [var.dominio]
  }
}

resource "google_compute_ssl_policy" "moderna" {
  name            = "${local.prefijo}-tls-moderna"
  profile         = "MODERN"
  min_tls_version = "TLS_1_2"
}

resource "google_compute_target_https_proxy" "web" {
  name             = "${local.prefijo}-web"
  url_map          = google_compute_url_map.web.id
  ssl_certificates = [google_compute_managed_ssl_certificate.web.id]
  ssl_policy       = google_compute_ssl_policy.moderna.id
}

resource "google_compute_global_forwarding_rule" "https" {
  name                  = "${local.prefijo}-https"
  target                = google_compute_target_https_proxy.web.id
  port_range            = "443"
  ip_address            = google_compute_global_address.publica.id
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

resource "google_compute_url_map" "a_https" {
  name = "${local.prefijo}-a-https"

  default_url_redirect {
    https_redirect         = true
    strip_query            = false
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
  }
}

resource "google_compute_target_http_proxy" "a_https" {
  name    = "${local.prefijo}-a-https"
  url_map = google_compute_url_map.a_https.id
}

resource "google_compute_global_forwarding_rule" "http" {
  name                  = "${local.prefijo}-http"
  target                = google_compute_target_http_proxy.a_https.id
  port_range            = "80"
  ip_address            = google_compute_global_address.publica.id
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

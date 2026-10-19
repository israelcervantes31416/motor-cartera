# La red del entorno: una VPC propia, una subred con acceso privado a las APIs de Google (Artifact
# Registry, Secret Manager, Cloud Logging) y el peering de servicios privados para Cloud SQL,
# Memorystore y Filestore. Nada de esto tiene IP publica; la unica entrada publica es el
# balanceador (balanceador.tf). Sin Cloud NAT: el worker y OSRM no salen a Internet.

resource "google_compute_network" "principal" {
  name                    = "${local.prefijo}-red"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "principal" {
  name                     = "${local.prefijo}-subred"
  region                   = var.region
  network                  = google_compute_network.principal.id
  ip_cidr_range            = "10.40.0.0/20"
  private_ip_google_access = true

  log_config {
    aggregation_interval = "INTERVAL_5_SEC"
    flow_sampling        = 0.5
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_global_address" "servicios_privados" {
  name          = "${local.prefijo}-servicios-privados"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.principal.id
}

resource "google_service_networking_connection" "servicios_privados" {
  network                 = google_compute_network.principal.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.servicios_privados.name]
}

# Por omision no entra nada. Se abren solo: el worker hacia OSRM (5000) y SSH a la VM de OSRM solo
# desde IAP, para mantenimiento.
resource "google_compute_firewall" "nada_entra" {
  name      = "${local.prefijo}-nada-entra"
  network   = google_compute_network.principal.id
  direction = "INGRESS"
  priority  = 65000

  deny {
    protocol = "all"
  }

  source_ranges = ["0.0.0.0/0"]
}

resource "google_compute_firewall" "worker_a_osrm" {
  name        = "${local.prefijo}-worker-a-osrm"
  network     = google_compute_network.principal.id
  direction   = "INGRESS"
  priority    = 1000
  source_tags = ["mc-worker"]
  target_tags = ["mc-osrm"]

  allow {
    protocol = "tcp"
    ports    = ["5000"]
  }
}

resource "google_compute_firewall" "ssh_por_iap" {
  name          = "${local.prefijo}-ssh-por-iap"
  network       = google_compute_network.principal.id
  direction     = "INGRESS"
  priority      = 1000
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["mc-osrm"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

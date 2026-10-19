# OSRM: una VM sin IP publica, con la red vial preparada en un disco persistente. La red es un
# activo derivado: se reconstruye desde su extracto de OpenStreetMap con `motor-cartera
# preparar-red-vial` y su preparar.sh (docs/backups.md), asi que el disco no se respalda. Solo el
# worker llega a su puerto (red.tf).
resource "google_compute_address" "osrm" {
  name         = "${local.prefijo}-osrm"
  region       = var.region
  subnetwork   = google_compute_subnetwork.principal.id
  address_type = "INTERNAL"
}

resource "google_compute_disk" "red_vial" {
  name = "${local.prefijo}-red-vial"
  zone = var.zona
  type = "pd-balanced"
  size = 50
}

resource "google_compute_instance" "osrm" {
  name                      = "${local.prefijo}-osrm"
  zone                      = var.zona
  machine_type              = "e2-highmem-2"
  tags                      = ["mc-osrm"]
  allow_stopping_for_update = true

  boot_disk {
    initialize_params {
      image = "projects/cos-cloud/global/images/family/cos-stable"
      size  = 20
    }
  }

  attached_disk {
    source      = google_compute_disk.red_vial.id
    device_name = "red-vial"
    mode        = "READ_ONLY"
  }

  network_interface {
    subnetwork = google_compute_subnetwork.principal.id
    network_ip = google_compute_address.osrm.address
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  service_account {
    email  = google_service_account.osrm.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    enable-oslogin         = "TRUE"
    block-project-ssh-keys = "TRUE"
    user-data = templatefile("${path.module}/osrm.cloud-init.yaml", {
      imagen = var.imagen_osrm
    })
  }
}

# Las sesiones de oauth2-proxy (revocables en el servidor al cerrar sesion): Memorystore for Redis,
# con AUTH y TLS. Su cadena de AUTH la genera Google y queda en el estado de OpenTofu (sensible): el
# bucket del estado es privado y solo lo lee la identidad de despliegue (docs/cloud-gcp.md).
resource "google_redis_instance" "sesiones" {
  name                    = "${local.prefijo}-sesiones"
  tier                    = local.produccion ? "STANDARD_HA" : "BASIC"
  memory_size_gb          = 1
  region                  = var.region
  redis_version           = "REDIS_7_2"
  authorized_network      = google_compute_network.principal.id
  connect_mode            = "PRIVATE_SERVICE_ACCESS"
  auth_enabled            = true
  transit_encryption_mode = "SERVER_AUTHENTICATION"

  depends_on = [google_service_networking_connection.servicios_privados]
}

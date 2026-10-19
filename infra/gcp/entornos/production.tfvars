# Production: un ejemplo. El proyecto, el dominio, el IdP y las imagenes son de quien despliega;
# estos valores solo sirven para validar la configuracion (los digests son de ceros a proposito).

proyecto       = "motor-cartera-produccion-ejemplo"
entorno        = "production"
dominio        = "cartera.example.com"
oidc_emisor    = "https://idp.example.com/realms/motor-cartera"
oidc_audiencia = "motor-cartera-api"

imagen_api          = "northamerica-south1-docker.pkg.dev/motor-cartera-produccion-ejemplo/motor-cartera/api@sha256:0000000000000000000000000000000000000000000000000000000000000000"
imagen_web          = "northamerica-south1-docker.pkg.dev/motor-cartera-produccion-ejemplo/motor-cartera/web@sha256:0000000000000000000000000000000000000000000000000000000000000000"
imagen_respaldo     = "northamerica-south1-docker.pkg.dev/motor-cartera-produccion-ejemplo/motor-cartera/respaldo@sha256:0000000000000000000000000000000000000000000000000000000000000000"
imagen_oauth2_proxy = "northamerica-south1-docker.pkg.dev/motor-cartera-produccion-ejemplo/motor-cartera/oauth2-proxy@sha256:0000000000000000000000000000000000000000000000000000000000000000"
imagen_osrm         = "northamerica-south1-docker.pkg.dev/motor-cartera-produccion-ejemplo/motor-cartera/osrm@sha256:0000000000000000000000000000000000000000000000000000000000000000"

sql_tier               = "db-custom-2-7680"
api_instancias_minimas = 1
api_instancias_maximas = 6

# La red vial

`ruteo/v2` rutea por calle sobre una **versión declarada de la red vial**: un extracto de
OpenStreetMap preparado con OSRM y servido por un `osrm-routed` **autoalojado**. Aquí está qué es
una versión, cómo se prepara, se publica y se sirve, y cómo el motor sabe que la red que le
contesta es la que se declaró. La política que la usa está en
[politica_ruteo.md](politica_ruteo.md) y el motor, en [ruteo_v2.md](ruteo_v2.md).

## Una referencia vial, no una fuente de negocio

La red vial pública es una **referencia**, como la cartografía municipal del INEGI: no es cartera ni
pagos, no entra al almacén de artefactos y ningún motor de negocio la lee. Solo la consulta el
proveedor de ruteo. OpenStreetMap se publica con la licencia **ODbL 1.0**: toda red cuyo
`proveedor_datos` es `OpenStreetMap` lleva en la API su atribución (`atribucion`: «Datos de la red
vial: (c) colaboradores de OpenStreetMap, licencia ODbL 1.0.»). Las redes grandes no entran al
repositorio: el `.gitignore` excluye `*.pbf`, `*.osm`, `*.osrm`, `*.osrm.*` y `*.tar`, y el control
de archivos trackeados reconoce un `.osm.pbf` y cada archivo preparado por OSRM (un tar) por sus
primeros bytes.

**Nunca se usa el servidor público de OSRM, Google Maps ni otro servicio externo**: las coordenadas
de las cuentas no salen de la red propia (decisión 155).

## Una versión: su huella, su manifiesto y su estado

Una versión (`version_red_vial`) la identifica su **huella**: el SHA-256 de su identidad en JSON
canónico, que es exactamente lo que determina la red que se rutea:

- el SHA-256 del archivo fuente (`.osm.pbf` u `.osm`), nunca su nombre;
- el motor (`OSRM`), su versión (`v26.9.0`) y su distribución exacta: la imagen de Docker por su
  digest (`docker:<imagen>`) o los binarios nativos por el SHA-256 de cada uno (`nativa:<sha>`);
- el algoritmo (`MLD`), el perfil (`car`) y el hash de sus archivos;
- los comandos exactos de la preparación, con sus parámetros.

La misma fuente con la misma distribución y los mismos comandos es la misma huella, en cualquier
máquina; cambiar la región que se declara, la fecha o el nombre del archivo no la cambia. La
preparación es otra cosa: los archivos que produce OSRM **no son idénticos byte por byte** entre dos
preparaciones (con el extracto de Puebla, 12 de los 26 cambian, entre ellos `.osrm.partition`, aun
con un solo hilo). Lo que responde sí es el mismo en lo que la política usa: los mismos snaps, la
misma conectividad y las mismas duraciones; las distancias pueden diferir en 0.1 m (en 214 de
30,000 celdas de una prueba con 300 puntos al azar en Puebla), y al redondear a metros, en un metro.
Por eso el SHA-256 de cada archivo preparado se guarda en el **manifiesto** como evidencia de esa
preparación, su hash (`hash_manifest`) entra en la firma de cada ejecución pero no en la huella, y
`registrar-red-vial` dice si un directorio es la preparación publicada u otra de la misma red: para
reconstruir una ejecución byte por byte, se sirve la publicada (decisión 157).

Una versión nace `EN_PROCESO` al empezar a prepararse y queda `PUBLICADA` (con su manifiesto
verificado, su `hash_manifest` y su cobertura: nodos, vías, relaciones, restricciones de giro y
aristas) o `FALLIDA` (con su motivo). Ya terminada no cambia ni se borra (un trigger lo impide), y
una huella se publica una sola vez: volver a registrarla la reutiliza.

## OSRM v26.9.0, fijado

| Qué | Valor |
|---|---|
| Versión | OSRM `v26.9.0` (septiembre de 2026) |
| Imagen | `ghcr.io/project-osrm/osrm-backend:v26.9.0-debian@sha256:8a1b1bc938412f15f9b5b32d794c4ec6bf4a85dfbbabfa0a014b70b187edb53b` |
| Algoritmo | MLD (Multi-Level Dijkstra): `extract`, `partition` y `customize` |
| Perfil | `car.lua` de OSRM v26.9.0, copiado tal cual en `src/motor_cartera/ruteo_v2/perfiles/osrm-v26.9.0/` con su licencia, y verificado contra el hash fijado en el código |

Nunca `latest` ni una etiqueta que se mueva: la imagen va por su digest en `preparar.sh`, en el
compose y en el CI, y una prueba comprueba que son la misma. v26.9.0 porque acepta las peticiones
por `POST` con un cuerpo JSON (las coordenadas no viajan en la URL) y porque `osrm-extract
--data_version` graba la red en cada respuesta (decisión 156).

Los comandos de la preparación, para una fuente `fuente/<archivo>`:

```text
osrm-extract -p perfil/car.lua --small-component-size 1 --data_version motor-cartera/red-vial/v1:<huella> -o red fuente/<archivo>
osrm-partition red.osrm
osrm-customize red.osrm
```

- `--data_version` graba la huella en la red: `osrm-routed` la declara en cada respuesta.
- `--small-component-size 1`: con el valor por omisión (1,000), OSRM trata distinto los
  componentes chicos de la red, y el snap de un punto en una tabla depende de los **demás** puntos
  de la petición. Con 1, cada punto tiene el mismo snap en cualquier petición y en cualquier bloque:
  la matriz de un gestor no depende de cómo se parta (decisión 159).

## Preparar, publicar y servir una red

```bash
# Con Docker: escribe preparar.sh, con la imagen fijada, y la version queda EN_PROCESO.
motor-cartera preparar-red-vial datos/red_vial/fuente/puebla.osm.pbf \
  --region "Estado de Puebla, Mexico" --fecha-snapshot 2026-10-13 \
  --proveedor-extracto "OpenStreetMap France" --url-fuente <url> --docker
sh datos/red_vial/<huella>/preparar.sh
motor-cartera registrar-red-vial datos/red_vial/<huella>

# Sin Docker: con los binarios nativos de OSRM v26.9.0, prepara y publica en un paso.
motor-cartera preparar-red-vial datos/red_vial/fuente/puebla.osm.pbf --region "..." \
  --osrm-bin <directorio de los binarios>
```

`preparar-red-vial` copia la fuente y el perfil al directorio de la huella
(`datos/red_vial/<huella>/`), escribe su `identidad.json` (la primera declaración se conserva) y
abre la versión `EN_PROCESO`. `registrar-red-vial` no confía en ningún nombre: verifica que la
fuente copiada tenga el SHA-256 de su identidad, que el perfil sea el fijado, que la huella sea la
del directorio y que estén todos los archivos que necesita `osrm-routed`; escribe el manifiesto y
publica. Si algo no cuadra, la versión queda `FALLIDA` con el motivo y el comando termina con
código 1. Con `--verificar-backend`, además comprueba que el OSRM de `MC_OSRM_URL` declara esta red,
sin enviarle ningún punto.

`preparar.sh` corre cada comando en un contenedor de la imagen fijada, con su bitácora. Al final,
el mismo contenedor deja legibles para todos los archivos preparados. OSRM crea el `.fileIndex`
solo para su dueño, que en el contenedor es root, y quien registra la red o la sirve puede ser otro
usuario: sin ese paso, `registrar-red-vial` no podría calcular su SHA-256.

En el compose, el servicio `osrm` (en el perfil `ruteo`, con la misma imagen) sirve la red de
`MC_RED_VIAL_HUELLA`, **sin puerto publicado**: solo la API y el worker lo ven. Arranca con
`--verbosity WARNING` porque su bitácora de accesos (INFO) repite cada petición con sus
coordenadas, y con `--max-nearest-locations 100` para el snap por lotes:

```bash
MC_RED_VIAL_HUELLA=<huella> docker compose --profile ruteo up -d --wait osrm
```

Sin Docker, el mismo servidor con los binarios nativos:

```bash
osrm-routed --algorithm mld --verbosity WARNING --max-nearest-locations 100 --port 5000 datos/red_vial/<huella>/red.osrm
```

y `MC_OSRM_URL=http://localhost:5000` en el proceso del worker.

## El backend declara su red

Cada respuesta de `osrm-routed` trae su `data_version`, `motor-cartera/red-vial/v1:<huella>`. El
motor, antes de enviar el punto de una cuenta o de una base, consulta `nearest` de (0, 0), un punto
que no es de nadie: si el backend declara otra red, la ejecución termina `FALLIDA` por
`RED_VIAL_BACKEND_INCOMPATIBLE` sin haber enviado nada. Y cada respuesta posterior se verifica igual:
una red que cambia a la mitad también falla cerrado (decisión 158).

El cliente (`ruteo_v2.proveedor`) habla con OSRM por `POST` con JSON, con una conexión persistente
por hilo y tiempos de espera acotados; reintenta con espera exponencial solo un fallo transitorio (un
5xx, un 429, una conexión rota o una respuesta que no es JSON) y nunca un error de configuración. Sus
errores no llevan la petición, ni la URL completa, ni la causa original: solo el servicio, el tipo y
los intentos. El snap va por lotes de 100 puntos (`nearest`, `number=1`, sin hints) y la matriz, por
bloques de a lo más 100 × 100 (`table`, con `duration` y `distance`, sin waypoints). El worker solo
toma trabajos `RUTEO_V2` cuando su proveedor responde, sin gastar intentos mientras no.

## La red sintética de las pruebas

`motor-cartera generar-red-sintetica` escribe una red **inventada** en OSM XML: una cuadrícula de 4 ×
4 cruces cada 1,000 m (calles residenciales y una avenida), una calle de un solo sentido que hace la
matriz asimétrica, y un tramo aislado de tres nodos sin camino a la cuadrícula. Son 19 nodos y 9
vías; el mismo origen da los mismos bytes. La prepara el mismo OSRM que una red real, así que el CI
prueba el snap, la matriz, la ruta y la conectividad con el motor de verdad, sin descargar nada de
OpenStreetMap: en el job de pruebas se prepara con la imagen fijada y la sirve un `osrm-routed` para
`tests/test_ruteo_v2_osrm.py`, y en el compose se dibuja dentro de un municipio de la referencia
sintética, con los puntos de la prueba de humo junto a sus calles (uno a 800 m de ellas y otro junto
al tramo aislado).

## Lo que cuesta una red real

Medido con el extracto estatal de Puebla de OpenStreetMap France (36.9 MiB, 3,987,559 nodos, 580,636
vías, 447 restricciones de giro y 1,739,426 aristas), con los binarios nativos en esta máquina
(Windows 11, 12 CPU):

| Paso | Segundos | Memoria pico |
|---|---:|---:|
| `osrm-extract` | 24.5 | 1,033 MiB |
| `osrm-partition` | 8.9 | 411 MiB |
| `osrm-customize` | 2.1 | 317 MiB |

Los 26 archivos preparados ocupan 388 MiB. `osrm-routed` responde a los 0.5 s de arrancar y, después
de rutear el plan XL dos veces, su memoria pico fue de 300 MiB. Con 100 puntos distintos al azar en
Puebla y un solo hilo, una tabla de 26 × 26 tarda 49 ms y una de 100 × 100 (el bloque más grande de
la política), 290 ms; el snap de 100 puntos, 6 ms. El ruteo de un plan contra esta red está en
[ruteo_v2.md](ruteo_v2.md).

## Lo que la red no tiene

No hay tráfico ni velocidades por hora del día: las duraciones son las del perfil `car`, estáticas.
No hay casetas, preferencias de vía, perfiles de camión ni restricciones por vehículo. Una red no se
actualiza sola: un extracto nuevo es otra versión, con otra huella, que alguien prepara, publica y
declara; ni la API ni el CLI usan nunca «la última» red.

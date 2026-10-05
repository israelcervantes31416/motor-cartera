# CARRIER: la hoja compañera de la cartera oficial

En los archivos de cartera del acreedor, junto a la hoja CARTERA aparece otra hoja, CARRIER. v0.6 la
trata como lo que es: una **hoja compañera observada**. Se reconoce, se audita y su resultado queda
en la evidencia de la corrida, pero **no se publica, no se proyecta y no gobierna la publicación de
CARTERA**. La tabla autoritativa es CARTERA ([diccionario](diccionario_cartera.md)).

## Su forma

CARRIER es CARTERA "a lo largo" por teléfono: una fila por cada teléfono distinto de cada cliente,
con las demás columnas de su fila y el teléfono en una sola columna, `TELEFONO`. Por eso tiene **85
columnas**: las 93 de CARTERA menos sus 9 columnas de teléfonos, más `TELEFONO`.

Las 9 que no trae:

| Columna de CARTERA | Qué era |
|---|---|
| `TEL_AVAL` | Teléfono del aval |
| `TELEFONO1` a `TELEFONO4` | Teléfonos del cliente |
| `TIPOTEL1` a `TIPOTEL4` | Tipo de cada teléfono |

La que agrega, en la posición donde CARTERA tiene `TELEFONO1`:

| Columna | Qué es |
|---|---|
| `TELEFONO` | Uno de los teléfonos del cliente o de su aval, de 10 dígitos |

El orden de las columnas no es parte de la estructura que se reconoce: CARRIER se reconoce por su
conjunto de columnas. El orden en que la escribe el generador es el de `COLUMNAS_CARRIER` en
`contratos/cartera_v2.py`.

## Dónde se busca

- **En un xlsx**, en la hoja que se llame CARRIER (sin distinguir mayúsculas). Si hay dos con ese
  nombre, no se audita ninguna y queda una advertencia.
- **En un zip**, en el miembro cuyo nombre contiene CARRIER, como `CARRIER.csv`. Si hay más de
  uno, tampoco se audita ninguno y queda una advertencia.
- **Un csv suelto** es una sola tabla: no tiene CARRIER.

Si el archivo no trae CARRIER, la corrida sigue igual: es opcional.

## Qué se audita

Sin juzgarla registro por registro, la auditoría dice:

- **Si su estructura es la esperada**: las 85 columnas, sin encabezados repetidos, sin columnas sin
  nombre. Si no, `estructura_reconocida` es falso y la advertencia dice qué falta o qué sobra.
- **Cuántas filas trae**, y si no trae ninguna.
- **Cuántas filas traen un `CLIENTE_UNICO` que no está en CARTERA**: CARRIER no puede introducir
  clientes que la tabla principal no tiene.
- **Cuántas filas traen un `TELEFONO` sin la forma de 10 dígitos.**

Cada problema es una **advertencia**, no un rechazo: CARTERA se publica o no por sus propias reglas.
El resultado queda en la tabla `hoja_companera` (nombre, filas, columnas, si la estructura se
reconoció y sus advertencias), en el detalle de la corrida y en
`GET /corridas/{run_id}/fuente`, en `hojas_companeras`.

## Por qué no se publica

Todo lo que CARRIER dice ya está en CARTERA: sus clientes son los de CARTERA y sus teléfonos son las
columnas anchas de cada fila. Publicarla duplicaría los datos de contacto en una segunda forma sin
agregar nada que la plataforma use todavía. Lo que sí importa es saber si el archivo la trae y si es
coherente con la tabla principal, porque una CARRIER incoherente es una señal de que el archivo no se
armó bien. El archivo original, con CARRIER incluida, se conserva entero en el almacén de
artefactos: si una versión posterior necesita sus filas, están ahí.

## En el generador sintético

`generar-oficial` escribe CARRIER en xlsx y en zip (salvo con `--no-carrier`), derivada de cada
bloque de CARTERA: una fila por cada teléfono distinto de `TELEFONO1` a `TELEFONO4` y de `TEL_AVAL`.
No agrega ningún cliente ni ningún teléfono que CARTERA no traiga. Los teléfonos sintéticos empiezan
con 0, así que no se le puede marcar a nadie.

# La partición operativa de campo

`campo/v2` ubica cada candidata de campo asignada a una base en cuatro unidades anidadas: zona,
subzona, tramo y microtramo. La partición es una **cuadrícula fija, no un agrupamiento**: no depende
de cuántas cuentas haya, de dónde estén las demás ni del orden en que lleguen. Una cuenta cae en las
mismas unidades hoy, mañana, en otra máquina o al reconstruir el plan, y agregar o quitar cuentas
no mueve a ninguna otra. Sobre ella se reparte la capacidad ([campo_v2.md](campo_v2.md)).

No hay k-means, ni clustering por densidad, ni polígonos dibujados a mano, ni rutas: ninguna unidad
se ajusta a las cuentas.

## Las cuatro unidades

| Nivel | Qué es | Clave dentro de su base |
|---|---|---|
| `ZONA` | La base y la entidad (los dos primeros dígitos del CVEGEO) del municipio espacial de la cuenta. | `CVE_ENT` |
| `SUBZONA` | La base y el municipio espacial (CVEGEO) de la cuenta. | `CVEGEO` |
| `TRAMO` | La celda de **5,000 m** de la cuadrícula en EPSG:6372 donde cae el punto de la cuenta, dentro de su municipio y su base. | `CVEGEO:tx:ty` |
| `MICROTRAMO` | La celda de **1,000 m** donde cae el punto, dentro de su tramo. | `CVEGEO:ix:iy` |

El municipio espacial es el de la geografía publicada de la cuenta ([geografia.md](geografia.md)),
y la base, la de su asignación de `territorial/v2` ([territorial_v2.md](territorial_v2.md)): solo
una candidata `ASIGNADA` en `territorial/v2` tiene partición. Una celda que cruza la frontera de
dos municipios da un tramo (y un microtramo) en cada uno: **un tramo nunca cruza un municipio ni una
base**, y cada unidad tiene un solo padre.

```
ZONA        BASE_A · 21
└─ SUBZONA  BASE_A · 21114
   └─ TRAMO       BASE_A · 21114 · (579, 157)       5 km × 5 km
      └─ MICROTRAMO  BASE_A · 21114 · (2897, 787)   1 km × 1 km; con su propietario preferido
```

## La cuadrícula

| Dato | Valor |
|---|---|
| Sistema métrico | **EPSG:6372** (México ITRF2008 / LCC): una proyección cónica conforme de Lambert, en metros, que cubre el país entero con una sola cuadrícula. |
| Tramo | 5,000 m |
| Microtramo | 1,000 m; un tramo tiene exactamente 5 × 5 microtramos. |
| Precisión | Cada coordenada métrica al milímetro (`0.001`). |
| Política | `campo-public-v2`: los tamaños, el sistema y la regla de la celda son parte de su hash. |

Cada punto se guarda en EPSG:4326 (longitud, latitud) y se lleva a EPSG:6372 con PostGIS
(`ST_Transform`), una sola transformación por punto. La celda de una coordenada `x` en una
cuadrícula de lado `T` es:

```
k = floor(round(numeric(x), 3) / T)
```

con `numeric(x)` el valor de punto flotante de PostGIS llevado a `numeric` con 15 dígitos
significativos, y el redondeo al milímetro con los empates lejos del cero. **La celda `k` es el
intervalo [k·T, (k+1)·T)**: un punto exactamente en la frontera es de la celda que empieza ahí, y
uno un milímetro antes, de la anterior. El redondeo al milímetro existe para que un punto que vuelve
de la transformación como 4,999.9999999995 m quede, sin ambigüedad, en 5,000.000: la misma regla en
PostgreSQL y en Python (`politica.celda`), y las pruebas comparan las dos en cada frontera.

El tramo es la celda que contiene al microtramo: con lados múltiplos, `floor(ix / 5)` es exactamente
`floor(x / 5,000)`, así que un microtramo nunca queda en dos tramos.

## Identificadores deterministas

Cada unidad tiene un UUIDv5, derivado de la política, el nivel, el código de la base y su clave:

```
UUIDv5(dde3cf6b-37bc-4844-a7a2-cd83fd5dd381, "campo-public-v2:MICROTRAMO:BASE_A:21114:2897:787")
  = 74a6b655-21f4-579b-98aa-604888a5e13d
```

- **El espacio de nombres** (`dde3cf6b-37bc-4844-a7a2-cd83fd5dd381`) es fijo y es parte de la
  política: cambiarlo cambiaría cada unidad publicada.
- **El código de la base, no su identificador interno**: la misma base en otra versión de bases, o
  en otra base de datos, tiene las mismas unidades.
- **La misma clave es la misma unidad** en cualquier plan, en cualquier orden y en cualquier
  máquina; dos planes de fechas distintas con la misma base dicen lo mismo de una cuenta que no se
  movió. Cambiar el tamaño de una celda sería otra política, con otros identificadores.

Un plan publica en `unidad_territorial_campo` solo las unidades que ocupó, cada una con su nivel, su
padre, su base, su entidad y su municipio, cuántas candidatas cayeron en ella y cuántas quedaron
asignadas; un microtramo, además, con su **propietario preferido** (el primero de su ranking,
[campo_v2.md](campo_v2.md)). Cada asignación de una cuenta lleva sus cuatro identificadores.

## Lo que la partición no publica

Ninguna respuesta, bitácora, error, exportación ni la Cuenta 360 lleva el punto de una cuenta, su
coordenada métrica, el índice de su celda, el recuadro ni el centroide de una unidad: solo sus
identificadores opacos. Las tablas de paso que tienen el punto viven dentro de la transacción del
plan (`ON COMMIT DROP`), y un error se registra con su tipo, su SQLSTATE y sus marcos, nunca con su
mensaje, que podría citar una fila.

**Opaco no es secreto.** El identificador es una función pública de la política, el código de la
base y la clave: quien conozca la política, el código de la base y el municipio podría probar celda
por celda hasta encontrar el kilómetro cuadrado de un microtramo. Es la granularidad de una unidad
operativa (la que ve cualquiera que trabaje en campo), no la ubicación de una cuenta, y se acepta a
cambio de identificadores reproducibles sin un secreto que administrar. Si un despacho necesitara
que el identificador no revelara su kilómetro cuadrado, la salida es una política con otro esquema
de identificadores (con una llave), no ocultarlo en esta.

## Lo que la partición no hace

- **No usa distancias ni tiempos.** La geometría métrica solo ubica cada punto en su celda: nada se
  mide entre cuentas, ni de una cuenta a su base, ni a lo largo de una calle.
- **No es una ruta.** Un microtramo no dice en qué orden se visitan sus cuentas (v0.13).
- **No balancea bases.** Una cuenta nunca se mueve de la base que le dio `territorial/v2`, aunque
  otra tenga capacidad libre.
- **No es un agrupamiento.** Dos cuentas a 10 m pueden caer en microtramos distintos si una
  frontera pasa entre ellas, y así debe ser: la regla es fija.

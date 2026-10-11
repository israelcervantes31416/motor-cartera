# La configuración de campo

La **configuración de campo** dice, sobre una versión declarada de bases operativas
([bases_operativas.md](bases_operativas.md)), qué gestores de campo hay, de qué base es cada uno,
con qué calendario trabaja, cuántas cuentas puede atender al día y si está activo; qué calendarios
hay, con sus ventanas de cada día de la semana y sus excepciones en una fecha; y en qué zona
horaria se leen esas horas. `campo/v2` planea con una versión declarada de ella
([campo_v2.md](campo_v2.md)).

Es **configuración operacional versionada**, como las bases: no es una fuente oficial, ni la
cartera ni los pagos. La declara quien opera, se publica en versiones inmutables con su hash, y un
plan histórico sigue viendo la versión con que se hizo.

```
VersionConfiguracionCampo   una versión completa: nombre, versión de bases, zona horaria, hash
      │                     canónico y conteos; PUBLICADA y ya no cambia
      ├── CalendarioCampo           cada calendario, por su código
      │      ├── VentanaJornadaCampo        una ventana de un día de la semana, o de una excepción
      │      └── ExcepcionCalendarioCampo   una fecha: CERRADO o VENTANAS_ESPECIALES
      └── GestorCampo               cada gestor: código, base, calendario, cupo y si está activo
```

El código vive en `src/motor_cartera/campo_v2/configuracion.py`.

**Un gestor es un código, nunca una persona.** La configuración no tiene nombres, teléfonos,
correos, CURP, RFC, números de empleado ni ningún otro dato de una persona, y ninguna llave los
admite: un documento que trae `nombre` en un gestor se rechaza. El código de un gestor de campo no
se liga a la columna `Gestor` de un pago observado ni al `actor_ref` de un evento del lifecycle:
son otros vocabularios, de otras fuentes.

## El documento

Una versión es un documento JSON. Las bases, los gestores y los horarios de este ejemplo, y los de
las pruebas, son sintéticos:

```json
{
  "nombre": "Campo sintético, octubre",
  "bases_version_id": "5f0c3b8e-1d2a-4c6b-9e7f-0a1b2c3d4e5f",
  "zona_horaria": "America/Mexico_City",
  "calendarios": [
    {
      "codigo": "LV_PARTIDO",
      "semana": {
        "LUNES": [{"inicio": "09:00", "fin": "14:00"}, {"inicio": "16:00", "fin": "19:00"}],
        "MARTES": [{"inicio": "09:00", "fin": "14:00"}, {"inicio": "16:00", "fin": "19:00"}]
      },
      "excepciones": [
        {"fecha": "2026-11-16", "tipo": "CERRADO", "motivo": "Festivo"},
        {
          "fecha": "2026-12-24",
          "tipo": "VENTANAS_ESPECIALES",
          "motivo": "Media jornada",
          "ventanas": [{"inicio": "09:00", "fin": "13:00"}]
        }
      ]
    }
  ],
  "gestores": [
    {
      "codigo": "GESTOR_001",
      "base": "BASE_A",
      "calendario": "LV_PARTIDO",
      "capacidad_cuentas_dia": 20,
      "activo": true
    }
  ]
}
```

O un JSONL, un registro por línea, con `"registro"`: una línea `configuracion` (con `nombre`,
`bases_version_id` y `zona_horaria`), una por `calendario` y una por `gestor`, con sus mismos
campos.

| Campo | Regla |
|---|---|
| `nombre` | La etiqueta de la versión: un texto no vacío, de hasta 120 caracteres. No entra en el hash. |
| `bases_version_id` | El UUID de una versión de bases publicada: la de los gestores. |
| `zona_horaria` | Una zona IANA explícita (`America/Mexico_City`), que Python y PostgreSQL conocen. Nunca la del servidor. |
| `calendarios` | Una lista no vacía de calendarios, con códigos sin repetir. |
| `gestores` | Una lista no vacía de gestores, con códigos sin repetir. |

Un calendario:

| Campo | Regla |
|---|---|
| `codigo` | `^[A-Z0-9_-]{1,32}$`, único en la versión. |
| `semana` | Un objeto con los días que tienen jornada (`LUNES` ... `DOMINGO`); cada uno, una lista de ventanas. Un día que no está, o con la lista vacía, no tiene jornada. |
| `excepciones` | Opcional: una lista de excepciones, una por fecha. |

Una ventana es `{"inicio": "HH:MM", "fin": "HH:MM"}` en hora local de la zona de la versión: el
intervalo [inicio, fin). `inicio` va de `00:00` a `23:59`; `fin`, hasta `24:00`, que solo puede ser
un fin. El inicio es antes que el fin, y dos ventanas del mismo día (o de la misma excepción) no se
enciman: tocarse no es encimarse (`09:00-14:00` y `14:00-19:00` valen; `09:00-14:00` y
`13:00-17:00`, no).

Una excepción:

| Campo | Regla |
|---|---|
| `fecha` | Una fecha `AAAA-MM-DD` que existe, una sola vez por calendario. |
| `tipo` | `CERRADO` o `VENTANAS_ESPECIALES`. |
| `motivo` | Opcional: un texto de hasta 120 caracteres (`Festivo`, `Capacitación`). |
| `ventanas` | Solo con `VENTANAS_ESPECIALES`, y al menos una: las de ese día, en lugar de las de la semana. Una `CERRADO` no tiene ventanas. |

| Tipo de excepción | Qué quiere decir |
|---|---|
| `CERRADO` | Ese día el calendario no tiene jornada, aunque la semana diga otra cosa: un festivo. |
| `VENTANAS_ESPECIALES` | Ese día el calendario tiene estas ventanas, y no las de la semana: una media jornada. |

Un gestor:

| Campo | Regla |
|---|---|
| `codigo` | `^[A-Z0-9_-]{1,32}$`, único en la versión: `GESTOR_001`. Un código opaco, nunca un nombre. |
| `base` | El código de una base **activa** de la versión de bases: un gestor de una base inactiva no recibiría ninguna cuenta. |
| `calendario` | El código de un calendario de la versión. |
| `capacidad_cuentas_dia` | Un entero de JSON (no un texto: `"20"` no se adivina) entre 1 y 1,000: su cupo declarado. Un gestor sin cupo se declara inactivo. |
| `activo` | `true` o `false`. Un gestor inactivo sigue en la versión, pero no recibe cuentas. |

**Fail-closed.** Una configuración con cualquier problema se rechaza entera, sin publicar nada, y
el error dice todos los problemas juntos: una llave que no se conoce, un código repetido, una hora
que no es `HH:MM`, una ventana al revés o encimada con otra, dos excepciones en la misma fecha, un
cupo de 0 o de 2,000, una zona que no existe, un calendario o una base que no existen, una base
inactiva. No se completa nada por omisión. El tope de 1,000 cuentas al día es de cordura contra un
error de captura (2,000 en lugar de 20), no una regla de negocio.

## La jornada de un gestor en una fecha

`campo/v2` mira cada gestor en la fecha de operación del plan, en este orden:

1. Si está inactivo: `INACTIVO`.
2. Si su calendario tiene una excepción en esa fecha: `CERRADO_POR_EXCEPCION`, si es `CERRADO`; o
   `VENTANAS_ESPECIALES`, con las ventanas de la excepción. La excepción reemplaza a la semana.
3. Si no, las ventanas de ese día de la semana (el día ISO de la fecha: lunes 1, domingo 7):
   `JORNADA_SEMANAL` si tiene alguna, `SIN_JORNADA` si no.

Solo `JORNADA_SEMANAL` y `VENTANAS_ESPECIALES` están **disponibles**. Un gestor disponible tiene
su cupo declarado completo, aunque trabaje media jornada: **el cupo no se deriva de las horas**, y
una cuenta es una unidad de capacidad. Uno que no está disponible tiene capacidad 0.

Las ventanas se leen en la hora local de la zona de la versión, y la carga de cada gestor en un plan
dice además el instante UTC de cada una, calculado por PostgreSQL con su base de zonas horarias
(`timezone(zona, fecha + minutos)`): las 09:00 en `America/Mexico_City` el 1 de octubre de 2026
son las 15:00 UTC. Un cambio de horario en otra zona lo resuelve esa misma base, no una cuenta a
mano.

## Versiones, hash y reutilización

Cada documento válido se lleva a su **forma canónica** (`campo-configuracion/v1`): el **hash de la
versión de bases** (no su identificador, que es de cada base de datos), la zona, los calendarios por
código con sus siete días y sus ventanas en orden, sus excepciones por fecha, y los gestores por
código. Su SHA-256 es el **hash de la configuración**.

- El mismo contenido es la misma versión: publicar otra vez la misma configuración, en otro orden o
  con otro nombre, devuelve la versión que ya existía (`200`, `reutilizada: true`) y no crea otra.
- Cambiar un cupo, un calendario, una excepción, la base de un gestor o si está activo es otra
  configuración, con otro hash: se publica como **otra versión**, y los planes que usaron la
  anterior la siguen viendo igual.
- Lo publicado no se reescribe: un trigger rechaza cualquier `UPDATE` o `DELETE` sobre sus cinco
  tablas, y otro rechaza, al insertarlas, dos ventanas encimadas de un mismo calendario.
  `GET /campo/configuraciones/{id}` dice en `hash_verificado` si el hash, vuelto a calcular con lo
  que hay en sus tablas, es el que se guardó.
- Una versión solo existe `PUBLICADA`: se valida completa antes de escribir nada, y se escribe en
  una transacción, con sus ventanas y sus gestores por `COPY`. Dos publicaciones simultáneas del
  mismo contenido dan una sola versión.

| Estado de una versión | Qué quiere decir |
|---|---|
| `PUBLICADA` | Validada, con su hash, completa. El único estado: nunca existe a medias. |

## La API y la línea de comandos

| Método y ruta | Qué hace |
|---|---|
| `POST /campo/configuraciones` | Publica una versión. `201` y `Location` si es nueva; `200` con `reutilizada: true` si ese contenido ya estaba publicado. `422 CONFIGURACION_INVALIDA` con un detalle por problema; `422 ENTRADA_INVALIDA` si el documento no tiene la forma de una configuración (una llave de más, un cupo que es un texto). |
| `GET /campo/configuraciones` | Las versiones, la más reciente primero, con sus conteos por base. |
| `GET /campo/configuraciones/{configuracion_campo_version_id}` | Una versión con cada calendario, sus ventanas y sus excepciones, cada gestor con su cupo, y `hash_verificado`. `404 CONFIGURACION_NO_ENCONTRADA`. |

```bash
uv run motor-cartera cargar-campo campo.json
uv run motor-cartera cargar-campo campo.jsonl
uv run motor-cartera generar-campo-sintetico --bases <bases_version_id> --destino datos/campo.json \
  --gestores-por-base 20 --festivos 2026-11-16 --medias-jornadas 2026-12-24 --semilla 7
```

`cargar-campo` imprime si la versión es nueva o se reutilizó, sus conteos (por base), su hash y
`configuracion_campo_version_id`; termina con código 1 si no es válida. `generar-campo-sintetico`
escribe una configuración **sintética** para las bases activas de una versión: gestores
`GESTOR_0001`, `GESTOR_0002`..., con un cupo de una lista declarada, uno de tres calendarios
públicos del generador (turno partido de lunes a viernes, corrido de lunes a sábado, y uno de jueves
a domingo) y una fracción declarada de inactivos; la misma semilla da el mismo documento, byte por
byte. Nada sale de personas reales.

## Lo que la configuración no es

- **No es un horario de visitas.** Las ventanas dicen cuándo puede trabajar un gestor, no a qué hora
  visita a quién: no hay citas, ni orden de visitas, ni duración de una visita (v0.13).
- **No es factibilidad vial.** El cupo es una declaración operativa: cuántas cuentas atiende al día
  un gestor, sin tiempos de traslado ni distancias.
- **No tiene habilidades, vehículos ni costos**, ni pesos por tipo de visita.
- **No es una fuente externa** ni se sincroniza con un sistema de personal: la publica quien opera
  Motor Cartera.

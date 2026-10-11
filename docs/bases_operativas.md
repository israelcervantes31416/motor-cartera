# Las bases operativas

Una **base operativa** es un punto desde donde un despacho atiende cuentas en campo: una oficina, un
módulo, un punto de salida. Para Motor Cartera es **configuración operacional**, no datos de la
cartera ni una fuente externa: la declara quien opera, con su ubicación y los municipios que puede
atender, y se publica en versiones inmutables. `territorial/v2` asigna cada cuenta a una base de
una versión declarada ([territorial_v2.md](territorial_v2.md)).

```
VersionBasesOperativas    una versión completa de la configuración: nombre, versión del formato,
      │                   hash canónico, conteos; PUBLICADA y ya no cambia
BaseOperativa             cada base de esa versión: código, nombre, punto (EPSG:4326), activa
      │
CoberturaBaseMunicipio    los municipios (CVEGEO) que esa base puede atender, declarados
```

El código vive en `src/motor_cartera/bases/configuracion.py`.

## El documento

Una versión es un documento JSON con su nombre y sus bases:

```json
{
  "nombre": "Bases del despacho, octubre",
  "bases": [
    {
      "codigo": "BASE_CENTRO",
      "nombre": "Base centro",
      "lat": 19.05,
      "lon": -98.2,
      "activa": true,
      "municipios": ["21114", "21015"]
    }
  ]
}
```

O un JSONL, una base por línea, con el nombre de la versión aparte (`--nombre`). Las coordenadas de
estos ejemplos, y las de las pruebas, son sintéticas.

| Campo | Regla |
|---|---|
| `codigo` | `^[A-Z0-9_-]{1,32}$`, único en la versión. |
| `nombre` | Un texto no vacío, de hasta 120 caracteres. |
| `lat`, `lon` | Números de JSON (no textos: `"19,05"` no se adivina), finitos, en [−90, 90] y [−180, 180], y no (0, 0). Se guardan en EPSG:4326. |
| `activa` | `true` o `false`. Una base inactiva sigue en la versión, pero no es elegible. |
| `municipios` | Una lista no vacía de CVEGEO de cinco dígitos, sin repetir: **la cobertura**. |

**Fail-closed.** Una configuración con cualquier problema se rechaza entera, sin publicar nada, y el
error dice todos los problemas juntos: un código repetido, una coordenada fuera de rango o (0, 0),
una cobertura vacía, repetida o que no es un CVEGEO, una llave que no se conoce (`capacidad`, por
ejemplo: v0.11 no la tiene). No se completa nada por omisión.

**La cobertura se declara, nunca se infiere.** Una base no atiende un municipio por estar cerca de
él, ni porque su punto caiga dentro: lo atiende si su configuración lo dice. La cercanía solo
decide entre las bases que ya cubren el municipio de una cuenta.

## Versiones, hash y reutilización

Cada documento válido se lleva a su **forma canónica** (`bases-operativas/v1`): las bases ordenadas
por código, sus municipios ordenados, los nombres en Unicode NFC y las coordenadas redondeadas a 7
decimales de grado (alrededor de un centímetro). Su SHA-256 es el **hash de la configuración**.

- El mismo contenido es la misma versión: publicar otra vez la misma configuración, en otro orden o
  con otro nombre de versión, devuelve la versión que ya existía (`200`, `reutilizada: true`) y no
  crea otra. El nombre de la versión es una etiqueta: no entra en el hash.
- Cambiar cualquier dato de una base (su nombre, su punto, si está activa, su cobertura) es otra
  configuración, con otro hash, y se publica como **otra versión**. La anterior no cambia: las
  ejecuciones de `territorial/v2` que la usaron la siguen viendo igual.
- Lo publicado no se reescribe: un trigger rechaza cualquier `UPDATE` o `DELETE` sobre las tres
  tablas, y `GET /bases/versiones/{id}` dice en `hash_verificado` si el hash, vuelto a calcular con
  lo que hay en sus tablas, es el que se guardó.
- Una versión solo existe `PUBLICADA`: se valida completa antes de escribir nada, y se escribe en
  una transacción. Dos publicaciones simultáneas del mismo contenido dan una sola versión.

## La API y la línea de comandos

| Método y ruta | Qué hace |
|---|---|
| `POST /bases/versiones` | Publica una versión. `201` y `Location` si es nueva; `200` con `reutilizada: true` si ese contenido ya estaba publicado. `422 CONFIGURACION_INVALIDA` con un detalle por problema; `422 ENTRADA_INVALIDA` si el documento no tiene la forma de una versión. |
| `GET /bases/versiones` | Las versiones, la más reciente primero. |
| `GET /bases/versiones/{bases_version_id}` | Una versión con cada base, su punto, si está activa y su cobertura, y `hash_verificado`. `404 BASES_NO_ENCONTRADAS`. |

```bash
uv run motor-cartera cargar-bases bases.json
uv run motor-cartera cargar-bases bases.jsonl --nombre "Bases del despacho, octubre"
```

`cargar-bases` imprime si la versión es nueva o se reutilizó, sus conteos, su hash y
`bases_version_id`. Termina con código 1 si la configuración no es válida.

## Lo que una base no es, todavía

- **No es capacidad.** Una base puede recibir 10 o 100,000 cuentas en v0.11, y eso no dice que pueda
  atenderlas: no hay gestores, ni jornadas, ni cargas, ni límites (v0.12 en adelante).
- **No es una zona.** No hay zonas, subzonas ni tramos territoriales; la cobertura es una lista de
  municipios.
- **No es una fuente externa.** No se importa de un sistema del acreedor ni se sincroniza: la
  publica quien opera Motor Cartera.

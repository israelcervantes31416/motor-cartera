# La orquestación durable

La cola sobre PostgreSQL, el worker y el flujo automático: cómo se registra, se reparte, se recupera
y se encadena el trabajo de cada motor. Sus métricas, alertas y runbooks están en
[observability.md](observability.md) y [runbooks/](runbooks/README.md).

**La API no ejecuta ningún motor.** Cada `POST` que pide trabajo registra el recurso `EN_PROCESO`
y su trabajo en PostgreSQL, en la misma transacción, y responde `201`. Un worker, en otro proceso,
toma el trabajo, ejecuta el motor y lo cierra:

```
API
 │
 ├── PostgreSQL, en una sola transacción
 │      ├── el recurso EN_PROCESO: la corrida y su archivo, o la ejecución de una etapa
 │      ├── TrabajoOrquestacion: su trabajo, en la cola
 │      └── FlujoOrquestacion: en qué etapa va la corrida
 │
 └── responde 201

worker (uno o varios procesos)
 │
 ├── toma un trabajo con FOR UPDATE SKIP LOCKED
 ├── lease: el trabajo es suyo hasta lease_hasta
 ├── latido: renueva el lease mientras trabaja
 └── ejecuta el motor y, al cerrar el trabajo, encola la etapa siguiente en la misma transacción
```

**El flujo automático.** Un solo `POST /corridas` produce la corrida, su decisión, su
organización territorial y su ruteo, sin que el cliente pida cada etapa:

```
INGESTA ──▶ DECISION ──▶ TERRITORIAL ──▶ RUTEO ──▶ COMPLETADA
```

Cuando el trabajo de una etapa se cierra con su recurso `EXITOSA`, la misma transacción abre la
siguiente; una etapa que no termina `EXITOSA` detiene el flujo. Todo se observa en
`GET /corridas/{run_id}/flujo` y `GET /flujos/{flujo_id}/trabajos`.

**Fuera del flujo, en la misma cola.** La historia de cada dataset (`HISTORIA`, v0.7) y la
interpretación de cada ventana de pagos (`MOTOR_PAGOS`, v0.8) son trabajos durables como
cualquier otro, pero ninguna etapa los espera y una falla suya no detiene nada. Entre los trabajos
que se pueden tomar, el worker toma primero los operacionales, después los `HISTORIA` y al final
los `MOTOR_PAGOS`.

**Entrega al menos una vez.** La entrega de un trabajo es *at-least-once*, no *exactly-once*: si un
worker muere después de que su motor confirmó y antes de cerrar el trabajo, otro worker lo vuelve a
tomar y llama otra vez al motor. Lo que no se repite es la publicación. Los motores ya estaban
protegidos de dos ejecuciones: toman su recurso con `FOR UPDATE`, no vuelven a ejecutar uno que
llegó a un estado terminal, publican todo o nada en una sola transacción, su fallo no degrada un
estado terminal, y los índices únicos admiten a lo más una ejecución `EXITOSA` y una `EN_PROCESO`
por fuente y versión.

**Lease y latido.**

```
PENDIENTE ──(un worker lo toma)──▶ EJECUTANDO ──(su recurso terminó)──▶ COMPLETADO
    ▲                                │   ▲
    │                                │   └── latido: renueva el lease
    └───(error del worker, espera)───┤
                                     └──(sin intentos)──▶ FALLIDO
```

Si el worker muere, deja de latir, su lease vence y otro worker toma el trabajo, con un intento
más. Si murió a media transacción del motor, PostgreSQL revierte lo que no se confirmó y el motor
se ejecuta entero otra vez; si murió después del `COMMIT`, el siguiente encuentra el recurso
terminado, no hace nada y cierra el trabajo. Un worker que perdió su lease ya no cierra el trabajo.
Con `SIGTERM` o `SIGINT`, el worker termina el trabajo en curso y sale.

**Trabajo no es motor.** El estado de un trabajo es el de la entrega; el de su recurso, el del
motor.

```
Trabajo COMPLETADO + EjecucionDecision FALLIDA + Flujo DETENIDO
```

no es una contradicción: el worker ejecutó bien un motor que terminó de forma controlada en
`FALLIDA`. Un error del worker (se cayó la conexión, murió el proceso) se reintenta solo, con
espera; una `FALLIDA` del motor, no, porque fallaría igual.

**Reanudar.** Un flujo detenido en la decisión, la organización territorial o el ruteo, con su
ejecución `FALLIDA`, se reintenta con `POST /flujos/{flujo_id}/reanudar`: otra ejecución de esa
etapa, y el flujo sigue hasta el ruteo. La ingesta no se reanuda, porque una corrida terminada es
evidencia inmutable: se vuelve a subir el archivo.

**El archivo.** Se copia al almacén de artefactos antes de responder, para que la ingesta
sobreviva a que la API muera, y no se borra nunca: es la evidencia de lo que llegó. Las corridas que
v0.5 dejó en la cola guardaban su archivo en PostgreSQL, y el worker las termina con él; las nuevas
no guardan archivos en la base.

**Parámetros del worker.** Son parámetros operativos, no reglas de decisión: cambian cuándo y
cuántas veces se intenta un trabajo, nunca qué calcula un motor.

| Variable | Por omisión | Qué controla |
|---|---|---|
| `MC_WORKER_POLL_SEGUNDOS` | `0.5` | Cuánto espera el worker antes de volver a buscar trabajo, con la cola vacía |
| `MC_WORKER_LEASE_SEGUNDOS` | `60` | Cuánto tiempo es suyo un trabajo sin latir; al vencer, otro worker lo puede tomar |
| `MC_WORKER_HEARTBEAT_SEGUNDOS` | `20` | Cada cuánto late para renovar el lease; tiene que ser menor que el lease |
| `MC_WORKER_MAX_INTENTOS` | `5` | Cuántas veces se puede tomar un trabajo antes de darlo por `FALLIDO`; cada trabajo copia el valor al nacer |
| `MC_WORKER_BACKOFF_SEGUNDOS` | `1` | La espera tras el primer error de un trabajo; se duplica en cada intento: 1, 2, 4, 8… |

El porqué de cada pieza está en [docs/decisiones.md](decisiones.md), secciones 42 a 53.

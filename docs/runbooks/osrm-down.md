# Runbook: OSRM no responde

En los comandos, `dcp` es `docker compose -f docker-compose.production.yml` ([índice](README.md)).

**Señal:** `mc_ruteo_disponible` en 0; la readiness del worker dice `ruteo: no_disponible`; trabajos
`RUTEO_V2` atrasados en la cola.

**Impacto:** solo el ruteo vial (`ruteo/v2`) espera. El worker **sigue listo** y sigue con los demás
trabajos: no toma un `RUTEO_V2` mientras su OSRM no responde, sin gastar sus intentos. Lo ya ruteado
sigue publicado.

## Qué esperar (medido en el simulacro)

Con OSRM detenido, el worker lo informa en 2.7 s (pregunta a lo más cada 10 s,
`MC_OSRM_SALUD_SEGUNDOS`) y sigue listo; al volver, el ruteo está disponible otra vez en 9.4 s.

## Diagnóstico

```bash
dcp --profile ruteo ps --all osrm
dcp --profile ruteo logs --since 15m osrm | tail -50
```

- **No arranca**: su log. Lo más común es que no encuentra la red: `MC_RED_VIAL_HUELLA` no nombra un
  directorio preparado en `datos/red_vial/<huella>` (con su `red.osrm*`).
- **Arranca pero declara otra red**: el worker exige que el `data_version` de cada respuesta sea el de
  la red publicada (`motor-cartera/red-vial/v1:<huella>`). Comprobarlo:
  `dcp exec -T worker motor-cartera registrar-red-vial datos/red_vial/<huella> --verificar-backend`.
- **Memoria**: una red real grande necesita más que el límite del compose (1 GiB).

## Mitigación

- `MC_RED_VIAL_HUELLA=<huella> dcp --profile ruteo up -d --wait osrm`.
- Si la red se perdió: es un activo derivado. Se vuelve a preparar desde su extracto de
  OpenStreetMap con `motor-cartera preparar-red-vial` y su `preparar.sh`, y su huella (que la base
  guarda) dice si es la misma ([red_vial.md](../red_vial.md)).

## Verificación

`mc_ruteo_disponible` en 1, la readiness del worker con `ruteo: ok` y la prueba de humo interna
("OSRM responde y tiene su red cargada", "OSRM declara la red que sirve").

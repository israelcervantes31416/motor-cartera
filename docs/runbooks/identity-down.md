# Runbook: la identidad no responde

En los comandos, `dcp` es `docker compose -f docker-compose.production.yml` ([índice](README.md)).

**Alertas:** `MCIdentidadNoDisponible`, `MCJwksFallando`; personas que no pueden iniciar sesión.

**Impacto:** depende de qué falla.

| Qué falla | Qué pasa |
| --- | --- |
| El IdP (Dex o el corporativo) | nadie puede **iniciar** sesión; las sesiones abiertas siguen mientras su token no venza y la API tenga la JWKS |
| La JWKS, por menos de una hora | nada: la API usa la que tiene (la descargó hace menos de una hora) |
| La JWKS, por más de una hora | la API responde `503 IDENTIDAD_NO_DISPONIBLE` a los tokens de persona: falla cerrada, nunca abierta. Las claves de servicio siguen funcionando |
| oauth2-proxy | el edge no puede validar sesiones: `/api/` responde `401`/`5xx`; la app pide iniciar sesión |
| Valkey (las sesiones) | las sesiones se pierden: cada persona inicia sesión otra vez |

## Diagnóstico

```bash
dcp ps --all dex oauth2-proxy valkey
dcp logs --since 15m oauth2-proxy dex | tail -100
dcp logs --since 15m api | grep -i jwks
```

- **`MCJwksFallando`**: la API no pudo descargar la JWKS (su log dice la clase del error: red, TLS,
  forma). Con un IdP corporativo, su estado; con Dex, `dcp ps dex`.
- **`TOKEN_CLAVE_DESCONOCIDA`** en la auditoría después de una rotación de llaves del IdP: la API
  vuelve a descargar la JWKS ante un `kid` desconocido (a lo más cada 30 s); si persiste, la JWKS del
  IdP no publica la llave con que firma.
- **Inicio de sesión que falla**: el log de oauth2-proxy (sin datos de la persona: `--auth-logging`
  está apagado) y el de Dex. Un reloj desfasado hace que un token aún no sea válido
  (`TOKEN_AUN_NO_VALIDO`): la tolerancia es de 30 s.

## Mitigación

- **Dex u oauth2-proxy caídos**: `dcp restart dex oauth2-proxy`.
- **Valkey caído**: `dcp restart valkey`; las sesiones se pierden (es un almacén en memoria, a
  propósito) y cada persona inicia sesión otra vez.
- **El IdP corporativo**: su equipo; mientras, nadie inicia sesión. No hay un modo sin autenticación
  (no existe: definir algo como `AUTH_DISABLED` impide arrancar).

## Verificación

La prueba de humo del edge (el inicio de sesión de cada usuario y sus permisos) y
`mc_jwks_refrescos_total{resultado="ok"}` subiendo.

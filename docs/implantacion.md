# Lo que falta para operar con un despacho real

Un backlog priorizado, revisado contra v1.0.2. motor-cartera es una plataforma estable
y verificable **sobre datos sintéticos**: certifica archivos, reconstruye la historia, decide
point-in-time, planea el campo y describe, predice y optimiza en sombra, con seguridad y
observabilidad de producción. Nunca procesó una cartera real ni la usó un despacho. Este documento
dice qué falta para eso, sin suponer una operación comercial que todavía no se validó. Lo que hoy no
hace está en [limitaciones.md](limitaciones.md); aquí se ordena por lo que bloquea.

**Cómo leerlo.**

- **Indispensable (P0):** sin esto no entra un dato personal real, ni en un piloto.
- **Recomendado (P1):** antes de operar de forma continua o con más de un despacho.
- **Futuro (P2):** cuando un uso real lo pida.

Ningún punto se resuelve con una configuración: cada uno es trabajo, una decisión del dueño del
producto o un acuerdo con el despacho.

## 1. Carteras y pagos reales

**Hoy.** Los contratos `cartera/v2` (93 columnas, con su hoja CARRIER) y `pagos/v1` (23) reproducen
un formato de origen concreto, con la estructura exacta y fail-closed
([fuentes.md](fuentes.md), [diccionario_cartera.md](diccionario_cartera.md)). Los archivos entran
por la API o la línea de comandos; no hay conectores.

- **P0.** Validar los contratos contra muestras reales anonimizadas del primer acreedor: cada
  diferencia de columnas, tipos o reglas es un contrato nuevo (`cartera/v3` o un adaptador
  declarado), nunca una relajación del actual.
- **P0.** El mapeo de cada acreedor a su contrato, escrito y versionado. Un despacho recibe
  carteras de varios acreedores con layouts distintos.
- **P1.** Una entrega automatizada y auditable (SFTP o un bucket con su manifiesto) en lugar de la
  subida manual, con el mismo almacén por contenido.
- **P2.** Conectores con los sistemas del acreedor y del despacho (CRM, marcador, pasarela de
  pagos).

## 2. Separación entre despachos y entre carteras

**Hoy.** Un despacho (`MC_DESPACHO_ID`) y una cartera (`MC_CARTERA_ID`) por despliegue. El valor
viaja en cada registro ([fuentes.md](fuentes.md#un-despacho-una-cartera)), pero los permisos son
globales: no hay un permiso por despacho ni por cartera.

- **P0.** Decidir el modelo de aislamiento. Para un piloto, **un despliegue por despacho** (base,
  almacén, secretos y usuarios propios): aísla por infraestructura, sin cambiar el código.
- **P0.** Varias carteras dentro de un despacho, porque un despacho trabaja para varios acreedores:
  la cartera de cada ingesta, cada consulta y cada motor declarada, y sus permisos acotados por
  cartera. Hoy la cartera sale de la configuración.
- **P2.** Multi-despacho en un solo despliegue (SaaS): el despacho en cada consulta y en cada
  permiso, con seguridad por filas en PostgreSQL y pruebas de que ninguna ruta cruza datos.

## 3. Autenticación, autorización y protección de datos

**Hoy.** OpenID Connect con PKCE detrás del edge, cuatro permisos por ruta en una matriz que falla
cerrada, claves de servicio por su hash, auditoría con seudónimos y logs que redactan
([security.md](security.md), [threat-model.md](threat-model.md)). Dex es solo para desarrollo.

- **P0.** El IdP del despacho (Entra ID, Google, Okta o Keycloak), con sus grupos `mc-*`, MFA y la
  baja de usuarios del lado del IdP.
- **P0.** Cifrado en reposo de la base, del almacén y de los respaldos, con llaves administradas.
  Hoy depende del disco del host.
- **P0.** Una revisión de seguridad externa (pentest) del despliegue real antes del primer dato
  real. Hoy no hubo ninguna.
- **P1.** TLS también en el tráfico interno (edge, API, base y OSRM) y una auditoría a prueba de
  manipulación, con su retención fuera del sistema.

## 4. Privacidad de la información financiera y personal

**Hoy.** `cartera/v2` trae datos personales del cliente y de terceros: nombres, domicilios,
teléfonos y datos del empleo y del aval. La app no los muestra, el edge no los deja pasar y los
logs los redactan ([privacidad_app.md](privacidad_app.md)). Pero el almacén guarda cada archivo
original **para siempre** y los snapshots son inmutables
([fuentes.md](fuentes.md#el-almacén-de-artefactos)).

- **P0.** Una política de retención y un **borrado o anonimización verificable**: por acreedor, por
  cartera y por persona, con su auditoría. Hoy ningún mecanismo borra un dato, y la inmutabilidad
  es parte del diseño: cambiarla es una decisión de arquitectura, no un script.
- **P0.** Los documentos legales con un abogado: el aviso de privacidad, el contrato de encargo
  entre el despacho y quien opere la plataforma, las transferencias entre el acreedor y el
  despacho, y la atención de los derechos ARCO. Las reglas de la cobranza en México (registro y
  disposiciones de CONDUSEF para despachos de cobranza) aplican al despacho; la plataforma tiene que
  dejarle la evidencia que necesite.
- **P1.** Minimizar: no conservar en el conformado las columnas personales que ningún motor usa.

## 5. Infraestructura y despliegue

**Hoy.** El compose de producción corre en una máquina con Docker, con todo endurecido
([production.md](production.md)). La referencia de Google Cloud en OpenTofu se valida en cada CI y
**nunca se aplicó**; le faltan, entre otras cosas, la plantilla del edge detrás del balanceador y el
IdP ([cloud-gcp.md](cloud-gcp.md#lo-que-falta-para-un-despliegue-real)).

- **P0.** Elegir el destino del piloto y aprobar su costo: completar y aplicar la referencia de
  GCP, o un servidor dedicado con el compose de producción. En los dos casos: un dominio, sus
  certificados y los secretos en un administrador de secretos.
- **P1.** Ambientes separados (staging y producción), despliegue por digest con aprobación
  ([decisión 302](decisiones.md#302-el-despliegue-solo-a-mano-con-aprobación-y-sin-llaves)) y
  PostgreSQL con alta disponibilidad (Cloud SQL regional).
- **P2.** Un almacén de objetos nativo (GCS) detrás de la interfaz del almacén.

## 6. Respaldos, restauración y recuperación

**Hoy.** `scripts/respaldo.py` respalda la base y el almacén juntos; la restauración se ensaya en
cada CI en una máquina limpia, con simulacros de respaldos alterados; el RTO objetivo es de una
hora ([backups.md](backups.md), [slos.md](slos.md#recuperación-ante-desastres-rpo-y-rto)).

- **P0.** Respaldos programados, cifrados y **fuera del sitio**, con retención acordada con el
  despacho, y una restauración ensayada en el ambiente real con el RPO y el RTO medidos.
- **P1.** PITR de la base (Cloud SQL o archivado de WAL) y un simulacro periódico de recuperación
  documentado.

## 7. Observabilidad y operación continua

**Hoy.** Logs JSON, métricas de Prometheus, trazas de OpenTelemetry, tableros de Grafana, alertas
con su runbook y objetivos de servicio iniciales ([observability.md](observability.md)).

- **P0.** Las alertas llegan a una persona: el canal de Alertmanager, una guardia y quién atiende
  cada runbook.
- **P1.** Retención de logs y auditoría en una plataforma administrada, monitoreo externo de
  disponibilidad y objetivos calibrados con tráfico real (burn rate).
- **P1.** Un proceso de cambios: ventanas de mantenimiento, migraciones con respaldo previo y
  vuelta atrás ensayada.

## 8. Carteras grandes

**Hoy.** XL (500,000 cuentas) es el escenario medido, en una máquina de desarrollo que no era
dedicada: la ingesta de un corte XL en 149 s, y la XXL (un millón) en 283 s
([README](../README.md#escala), [rendimiento.md](rendimiento.md)). Las tablas que crecen con cada
corte no se particionan y no hay retención de ejecuciones.

- **P0.** Una prueba de capacidad en la infraestructura elegida, con el volumen del despacho piloto
  y su frecuencia de cortes.
- **P1.** Particionar por corte las tablas que crecen y retirar las ejecuciones viejas según la
  retención. Dimensionar disco y WAL: una decisión XL escribe 513 MiB y 879 MiB de WAL
  ([limitaciones.md](limitaciones.md)).
- **P2.** Workers que escalen solos y prioridades por despacho o por urgencia en la cola.

## 9. Validación con usuarios

**Hoy.** Nadie la ha usado en una operación. La app es de solo lectura y ningún motor ejecuta una
acción de cobranza; la predicción y la optimización corren en sombra.

- **P0.** Un piloto en sombra con un despacho y una cartera: la plataforma recomienda, el despacho
  opera como siempre, y se comparan las dos cosas. Las métricas de éxito se acuerdan **antes** de
  empezar (cobertura de gestión, recuperación por cuenta asignada, tiempo de planeación) y se
  reportan sin afirmar causalidad, como el resto del sistema.
- **P0.** Sesiones con gestores, supervisores y la dirección del despacho sobre la app: qué
  pantalla usan, qué les falta y qué sobra.
- **P1.** Registrar las gestiones desde la operación (hoy solo por la API), si el piloto lo pide.
- **P2.** Acciones (asignar, exportar a un marcador o a mensajería) con su aprobación humana.

## 10. Implantación y comercialización

**Hoy.** El código es visible pero sin licencia de uso ([LICENSE](../LICENSE)), y no hay un modelo
de servicio.

- **P0.** El modelo: un servicio administrado o un despliegue en la infraestructura del despacho.
  Cada uno decide quién opera, quién es el encargado de los datos y qué se firma.
- **P0.** Una licencia o un contrato de uso, con soporte, niveles de servicio y responsabilidades
  escritas.
- **P1.** Una incorporación repetible: el taller del mapeo de cada acreedor, la carga histórica
  (backfill), la capacitación y los criterios para salir del piloto.
- **P2.** Precio por cuenta gestionada o por despacho, cuando el piloto mida el valor.

## El orden

| # | Qué | Área | Depende de |
| --- | --- | --- | --- |
| 1 | Retención y borrado o anonimización verificable de datos personales | 4 | una decisión de arquitectura |
| 2 | Documentos legales y modelo de servicio | 4, 10 | asesoría legal y el dueño del producto |
| 3 | Destino del piloto, cifrado en reposo, secretos e IdP | 3, 5 | 2 |
| 4 | Varias carteras por despacho, con permisos por cartera | 2 | — |
| 5 | Contratos validados con muestras reales anonimizadas | 1 | 2 y un acreedor |
| 6 | Respaldos fuera del sitio y restauración en el ambiente real | 6 | 3 |
| 7 | Alertas con guardia, prueba de capacidad y pentest | 3, 7, 8 | 3 y 5 |
| 8 | Piloto en sombra con métricas acordadas | 9 | todo lo anterior |

**La próxima prioridad técnica es la 1.** Es el único bloqueo que hoy se puede resolver solo con
código y que contradice el diseño actual: el almacén y los snapshots no se borran nunca, y un
tratamiento legítimo de datos personales exige poder borrarlos o anonimizarlos, con auditoría,
cuando vence su retención o lo pide el titular. Hacerlo antes que cualquier dato real evita
diseñar alrededor de una inmutabilidad que no se podrá sostener. Su versión la decide el diseño
([versioning.md](versioning.md)): en 1.x lo ya publicado no se reescribe, así que una operación
nueva y explícita, con su auditoría, puede llegar en un menor; una que cambie el significado de lo
publicado es un cambio mayor.

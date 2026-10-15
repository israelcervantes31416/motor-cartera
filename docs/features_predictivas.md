# Las features de `predictive-features/public-v1`

`predictive-features/public-v1` es la lista explícita y versionada de las 40 features de
`predictive/model-v1`: 37 numéricas y 3 categóricas, con 17 indicadores de nulo. **Ninguna se elige
sola**: no hay selección automática de columnas, y una columna nueva en una fuente no se vuelve una
feature. Su SHA-256 es

`786b5ef4b5a21a7583f59082196f77bc4900636ed1089e749fe3a726f9db5e94`

(`GET /predictivo/features` lo devuelve con la definición completa). Cada entrenamiento, cada versión
del modelo y cada scoring lo guardan; un servicio no puntúa con otra.

## Point-in-time

Una feature de un ancla (o de un scoring) usa **hechos ocurridos hasta el final de `feature_as_of`
(T) y registrados hasta `feature_known_at` (K)**. Nada posterior:

- los cortes de la cartera hasta T que ya existían a K (un corte que se materializó después no
  existe para ese ancla);
- la interpretación de pagos de cada mes **vigente a K** (la `EXITOSA` más reciente que ya había
  terminado) y sus movimientos recibidos hasta el final de T; un `PAGO` está anulado solo si el
  `REVERSO` que lo anula se recibió hasta el final de T: un reverso futuro no es una feature;
- el lifecycle ocurrido hasta el final de T y registrado hasta K, sin una anulación registrada
  hasta K (la regla de `decision/v2`: una anulación corrige el registro); un evento tardío,
  registrado después de K, no existe para la feature aunque haya ocurrido antes de T;
- la evaluación de promesas `EXITOSA` de la cartera, en la zona, con `as_of` hasta T y **terminada
  hasta K**, la de `as_of` más reciente; una evaluación terminada después de K no existe, aunque sea
  de un `as_of` anterior.

La **cobertura de pagos** de cada ventana se decide con la regla de `analytics/v1`: un día tiene
cobertura si cae en el alcance de algún archivo de pagos conocido a K y la interpretación vigente de
su mes clasificó cada pago observado conocido de ese mes. Una ventana sin cobertura completa deja sus
features de pagos NULL, nunca cero. Las mismas features se construyen con el mismo SQL en el
dataset de un entrenamiento, en un scoring y en la explicación de una cuenta.

## Las features

Las ventanas son de días de calendario en la zona declarada: [T−29, T] es la de 30 días que termina
en T; [T−89, T], la de 90. Un conteo del lifecycle sin eventos es 0; una feature de pagos de una
ventana sin cobertura, una de la evaluación sin evaluación conocida o una distancia en días sin
evento del cual medirla es NULL.

| Feature | Familia | Clase | Tipo | Transformación | Ventana | Fuente | NULL cuando | Indicador |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `saldo_total` | SNAPSHOT | NUMERICA | IMPORTE | LOG1P | al corte (T) | snapshot_cuenta.saldo_total | nunca | no |
| `saldo` | SNAPSHOT | NUMERICA | IMPORTE | LOG1P | al corte (T) | snapshot_cuenta.saldo | el corte no trae el dato | sí |
| `saldo_atrasado` | SNAPSHOT | NUMERICA | IMPORTE | LOG1P | al corte (T) | snapshot_cuenta.saldo_atrasado | el corte no trae el dato | sí |
| `saldo_requerido` | SNAPSHOT | NUMERICA | IMPORTE | LOG1P | al corte (T) | snapshot_cuenta.saldo_requerido | el corte no trae el dato | sí |
| `moratorios` | SNAPSHOT | NUMERICA | IMPORTE | LOG1P | al corte (T) | snapshot_cuenta.moratorios | el corte no trae el dato | sí |
| `pago_normal` | SNAPSHOT | NUMERICA | IMPORTE | LOG1P | al corte (T) | snapshot_cuenta.pago_normal | el corte no trae el dato | sí |
| `dias_atraso` | SNAPSHOT | NUMERICA | ENTERO | NINGUNA | al corte (T) | snapshot_cuenta.dias_atraso | nunca | no |
| `atraso_maximo` | SNAPSHOT | NUMERICA | ENTERO | NINGUNA | al corte (T) | snapshot_cuenta.atraso_maximo | el corte no trae el dato | sí |
| `producto` | SNAPSHOT | CATEGORICA | TEXTO | NINGUNA | al corte (T) | snapshot_cuenta.producto | nunca | no |
| `cve_entidad` | SNAPSHOT | CATEGORICA | TEXTO | NINGUNA | al corte (T) | snapshot_cuenta.cve_entidad | nunca | no |
| `tramo_atraso` | SNAPSHOT | CATEGORICA | TEXTO | NINGUNA | al corte (T) | snapshot_cuenta.dias_atraso | nunca | no |
| `numero_cortes_observados` | HISTORIA | NUMERICA | ENTERO | NINGUNA | cortes hasta T conocidos a K | snapshot_cuenta | nunca | no |
| `delta_saldo_1_corte` | HISTORIA | NUMERICA | IMPORTE | SIGNED_LOG1P | el snapshot anterior observado | snapshot_cuenta.saldo_total | la cuenta no tiene un snapshot anterior conocido a K | sí |
| `delta_saldo_4_semanas` | HISTORIA | NUMERICA | IMPORTE | SIGNED_LOG1P | el snapshot más reciente con fecha ≤ T−28 | snapshot_cuenta.saldo_total | la cuenta no tiene un snapshot conocido a K de 28 días o más antes de T | sí |
| `delta_dias_atraso_1_corte` | HISTORIA | NUMERICA | ENTERO | NINGUNA | el snapshot anterior observado | snapshot_cuenta.dias_atraso | como `delta_saldo_1_corte` | no |
| `delta_dias_atraso_4_semanas` | HISTORIA | NUMERICA | ENTERO | NINGUNA | el snapshot más reciente con fecha ≤ T−28 | snapshot_cuenta.dias_atraso | como `delta_saldo_4_semanas` | no |
| `tendencia_saldo` | HISTORIA | NUMERICA | DECIMAL | SIGNED_LOG1P | snapshots con fecha en [T−28, T] | snapshot_cuenta.saldo_total | la cuenta tiene menos de dos snapshots conocidos en [T−28, T] | sí |
| `tendencia_atraso` | HISTORIA | NUMERICA | DECIMAL | NINGUNA | snapshots con fecha en [T−28, T] | snapshot_cuenta.dias_atraso | como `tendencia_saldo` | no |
| `recuperacion_neta_30d_pasados` | PAGOS | NUMERICA | IMPORTE | SIGNED_LOG1P | [T−29, T] | movimiento_economico_canonico | la ventana de 30 días no tenía cobertura de pagos a K | sí |
| `recuperacion_neta_90d_pasados` | PAGOS | NUMERICA | IMPORTE | SIGNED_LOG1P | [T−89, T] | movimiento_economico_canonico | la ventana de 90 días no tenía cobertura de pagos a K | sí |
| `movimientos_30d` | PAGOS | NUMERICA | ENTERO | LOG1P | [T−29, T] | movimiento_economico_canonico | como `recuperacion_neta_30d_pasados` | no |
| `movimientos_90d` | PAGOS | NUMERICA | ENTERO | LOG1P | [T−89, T] | movimiento_economico_canonico | como `recuperacion_neta_90d_pasados` | no |
| `pagos_positivos_30d` | PAGOS | NUMERICA | ENTERO | LOG1P | [T−29, T] | movimiento_economico_canonico | como `recuperacion_neta_30d_pasados` | no |
| `pagos_positivos_90d` | PAGOS | NUMERICA | ENTERO | LOG1P | [T−89, T] | movimiento_economico_canonico | como `recuperacion_neta_90d_pasados` | no |
| `reversos_90d` | PAGOS | NUMERICA | ENTERO | LOG1P | [T−89, T] | movimiento_economico_canonico | como `recuperacion_neta_90d_pasados` | no |
| `dias_desde_ultimo_movimiento` | PAGOS | NUMERICA | ENTERO | LOG1P | toda la historia hasta T | movimiento_economico_canonico | no hay movimiento conocido, o la ventana de 90 días no tenía cobertura | sí |
| `dias_desde_ultima_recuperacion_positiva` | PAGOS | NUMERICA | ENTERO | LOG1P | toda la historia hasta T | movimiento_economico_canonico | no hay un PAGO no anulado conocido, o la ventana de 90 días no tenía cobertura | sí |
| `gestiones_30d` | LIFECYCLE | NUMERICA | ENTERO | LOG1P | [T−29, T] | gestion_cobranza | nunca | no |
| `gestiones_90d` | LIFECYCLE | NUMERICA | ENTERO | LOG1P | [T−89, T] | gestion_cobranza | nunca | no |
| `contactos_30d` | LIFECYCLE | NUMERICA | ENTERO | LOG1P | [T−29, T] | gestion_cobranza | nunca | no |
| `contactos_90d` | LIFECYCLE | NUMERICA | ENTERO | LOG1P | [T−89, T] | gestion_cobranza | nunca | no |
| `gestiones_sin_contacto_30d` | LIFECYCLE | NUMERICA | ENTERO | LOG1P | [T−29, T] | gestion_cobranza | nunca | no |
| `dias_desde_ultima_gestion` | LIFECYCLE | NUMERICA | ENTERO | LOG1P | toda la historia hasta T | gestion_cobranza | no hay gestión conocida | sí |
| `dias_desde_ultimo_contacto` | LIFECYCLE | NUMERICA | ENTERO | LOG1P | toda la historia hasta T | gestion_cobranza | no hay contacto conocido | sí |
| `promesas_creadas_90d` | PROMESAS | NUMERICA | ENTERO | LOG1P | [T−89, T] | promesa_pago | nunca | no |
| `promesas_cumplidas_historicas` | PROMESAS | NUMERICA | ENTERO | LOG1P | la evaluación conocida a K | evaluacion_promesa | no había una evaluación de promesas `EXITOSA` conocida a K con `as_of` hasta T en la zona | sí |
| `promesas_parciales_historicas` | PROMESAS | NUMERICA | ENTERO | LOG1P | la evaluación conocida a K | evaluacion_promesa | como `promesas_cumplidas_historicas` | no |
| `promesas_incumplidas_historicas` | PROMESAS | NUMERICA | ENTERO | LOG1P | la evaluación conocida a K | evaluacion_promesa | como `promesas_cumplidas_historicas` | no |
| `promesas_pendientes_actuales` | PROMESAS | NUMERICA | ENTERO | LOG1P | la evaluación conocida a K | evaluacion_promesa | como `promesas_cumplidas_historicas` | no |
| `dias_desde_ultima_promesa` | PROMESAS | NUMERICA | ENTERO | LOG1P | toda la historia hasta T | promesa_pago | no hay promesa conocida | sí |

Notas:

- `tramo_atraso` es `dias_atraso` en las fronteras canónicas de atraso (`0`, `1-30`, `31-60`,
  `61-90`, `91+`): una categórica deja a un modelo lineal una forma no lineal del atraso.
- `cve_entidad` (la clave INEGI de dos dígitos de la entidad) es la única geografía del modelo.
- La recuperación neta de 30 y 90 días son `PAGO` no anulados más `POSIBLE_REVERSO` de las
  interpretaciones vigentes a K; `reversos_90d` cuenta `REVERSO` y `POSIBLE_REVERSO`.
- Las tendencias son la pendiente de mínimos cuadrados contra los días, en `numeric` exacto (sin
  depender del orden en que la suma recorre las filas), con seis decimales.
- Una gestión sin contacto que es un mensaje de una vía (`NO_APLICA`) no cuenta como
  `gestiones_sin_contacto_30d`.
- Los indicadores de nulo (`<nombre>__nulo`) son de las features cuyo NULL tiene significado; las
  que comparten el mismo NULL que otra no repiten su indicador.

## Lo que no es una feature

`problemas_del_esquema` lo comprueba con cada nombre (y una prueba con cada feature); ninguna
feature nueva puede llamarse así:

| Excluido | Por qué |
| --- | --- |
| `cuenta_id`, `cuenta_canonica_id`, `cliente_unico`, `despacho_id`, `cartera_id`, `source_row`, `source_sheet`, `actor_ref` | Son identificadores. Un identificador como feature memoriza a la cuenta en lugar de aprender, y un **hash de una identidad sigue siendo una identidad**: tampoco un nombre, un teléfono o un domicilio codificados o hasheados. |
| nombre, domicilio, teléfono, referencias, aval, correo | Datos personales. |
| CURP, RFC, sexo o género, edad, religión, salud, etnia, CLABE | Datos sensibles: ninguno entra, ni como proxy. |
| latitud, longitud, punto, coordenadas, municipio, tramo, microtramo, zona, subzona | Geografía fina: identifica a una persona y no generaliza. La entidad sí entra. |
| `estrategia`, `canal`, `estatus_plan`, `estatus_promesa_pago` | Son la política declarada de la cobranza sobre la cuenta, no un dato de la cuenta: un modelo que los usa aprende la política, y un cambio de política lo invalida. |
| la prioridad, el canal recomendado o el score de `decision/v2`; la base, el gestor, el orden, la ETA o el estado de `campo/v2` y `ruteo/v2` | Son salidas de motores que decidirían *con* estas predicciones (v0.16): usarlas cerraría un ciclo. Las tablas de esas salidas no aparecen en el SQL de las features. |
| `semanas_atraso` | Es `ceil(dias_atraso / 7)`: redundante. |
| mes del año, día de la semana, semana ISO | Un modelo que aprende el calendario de TRAIN extrapola a meses que no vio. |

## Faltantes, escalado y categóricas

Todo lo aprendido sale **solo de TRAIN**:

- **Transformación.** Cada numérica pasa por la suya, declarada: `NINGUNA`, `LOG1P`
  (log(1 + max(x, 0)), para las sesgadas no negativas) o `SIGNED_LOG1P` (sign(x) · log(1 + |x|),
  para las sesgadas con signo). NULL sigue siendo NULL.
- **Imputación.** Un NULL se imputa con la **mediana de TRAIN** del valor transformado; si la
  feature tiene indicador, el indicador vale 1.
- **Escalado.** La numérica transformada e imputada se estandariza con la media y la desviación de
  TRAIN. Una con todos sus valores de TRAIN iguales es constante: su columna vale 0 y su coeficiente
  es exactamente 0.
- **Categóricas.** One-hot con el vocabulario de TRAIN en orden canónico (por código de carácter), y
  después `__MISSING__` (NULL) y `__UNKNOWN__` (una categoría que TRAIN no vio: nunca una columna
  nueva ni un error). Sin hashing.

Las estadísticas se redondean a 12 cifras significativas antes de construir el diseño de
entrenamiento, para que el modelo que se ajusta y el que se publica vean exactamente los mismos
valores. La matriz de diseño se construye por bloques de 65,536 filas, nunca entera.

## La forma canónica y el `feature_hash`

La forma canónica de las features de una cuenta es la versión, y una línea `nombre=valor` por
feature en su orden:

```text
predictive-features/public-v1
saldo_total=12500.00
saldo=~
…
producto=s7:CONSUMO
…
```

Los enteros en decimal, los importes con dos decimales, las pendientes con seis, el texto con su
largo delante (`s<largo>:<texto>`) y NULL como `~`; un `-0.00` es `0.00`. El `feature_hash` es el
SHA-256 de esa forma. PostgreSQL lo calcula al construir las features (lo guarda cada predicción) y
Python lo recalcula igual (`features.feature_hash`): la explicación de una predicción lo verifica, y
una prueba lo compara cuenta por cuenta.

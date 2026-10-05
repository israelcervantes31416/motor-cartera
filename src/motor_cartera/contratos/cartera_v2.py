"""cartera/v2: la cartera tal como la entrega el acreedor, con sus 93 columnas.

cartera/v1 sigue congelado: 8 columnas, sus alias y su firma, para que cada corrida que ya se juzgo
se pueda volver a explicar igual. cartera/v2 es otro contrato, y no lo reemplaza: es la hoja CARTERA
observada en los archivos del acreedor, con exactamente estas 93 columnas.

- Estructura exacta: las 93, ninguna mas, sin encabezados repetidos (ver `contratos.fuente`). Una
  desviacion es un error explicito, nunca un descarte silencioso.
- La fecha de corte no es una columna: las 93 no traen una. Es metadata del lote, que declara quien
  sube el archivo, y queda en la corrida. No hay una columna 94.
- CLIENTE_UNICO es el identificador fuente de la cuenta y no se repite dentro del corte. Si se
  repite, se rechazan todas sus copias: no hay forma de saber cual es la buena. La misma cuenta en
  otro corte es otra corrida, y es valida.
- Solo se tipan las columnas cuyo significado no es ambiguo por su nombre: fechas, importes,
  enteros, codigos postales, telefonos y coordenadas. Las demas se conservan como texto: su
  semantica no esta establecida, y tiparlas seria inventar reglas.
- PRODUCTO y CANAL usan los catalogos de cartera/v1, porque la proyeccion los copia a Cuenta sin
  traducirlos y los motores ya los conocen.

Los nombres de las columnas son los del contrato fuente que se simula, tal cual, incluidos los dos
que llevan un espacio: SALDO ATRASADO y SALDO REQUERIDO.
"""

from __future__ import annotations

from motor_cartera.contratos.cartera import CANALES, PRODUCTOS
from motor_cartera.contratos.fuente import Columna, ContratoFuente, Tipo

VERSION_CONTRATO_V2 = "cartera/v2"
"""Cambia si cambian las columnas, sus reglas o la forma canonica con que se firma el contenido."""

CLIENTE = r"[A-Z0-9]{8,20}"
"""La forma de CLIENTE_UNICO: la misma de cartera/v1, que es la que cabe en Cuenta."""
CODIGO_POSTAL = r"\d{5}"
TELEFONO = r"\d{10}"
"""Un telefono nacional de 10 digitos."""


def _texto(nombre: str) -> Columna:
    return Columna(nombre)


def _importe(nombre: str) -> Columna:
    return Columna(nombre, Tipo.IMPORTE)


def _contador(nombre: str) -> Columna:
    return Columna(nombre, Tipo.ENTERO, minimo=0)


COLUMNAS: tuple[Columna, ...] = (
    Columna("CLIENTE_UNICO", Tipo.CODIGO, requerida=True, patron=CLIENTE),
    _texto("NOMBRE_CTE"),
    _texto("GENERO_CLIENTE"),
    Columna("EDAD_CLIENTE", Tipo.ENTERO, minimo=0, maximo=130),
    _texto("OCUPACION"),
    _texto("DIRECCION_CTE"),
    _texto("NUM_EXT_CTE"),
    _texto("NUM_INT_CTE"),
    Columna("CP_CTE", Tipo.CODIGO, patron=CODIGO_POSTAL),
    _texto("COLONIA_CTE"),
    Columna("POBLACION_CTE", requerida=True),
    Columna("ESTADO_CTE", requerida=True),
    _texto("TERRITORIO"),
    _texto("TERRITORIAL"),
    _texto("ZONA"),
    _texto("ZONAL"),
    _texto("NOMBRE_DESPACHO"),
    _texto("GERENCIA"),
    Columna("FECHA_ASIGNACION", Tipo.FECHA),
    _contador("DIAS_ASIGNACION"),
    _texto("REFERENCIAS_DOMICILIO"),
    _texto("CLASIFICACION_CTE"),
    _texto("DIQUE"),
    _contador("ATRASO_MAXIMO"),
    Columna("DIAS_ATRASO", Tipo.ENTERO, requerida=True, minimo=0, maximo=3650),
    _importe("SALDO"),
    _importe("MORATORIOS"),
    Columna("SALDO_TOTAL", Tipo.IMPORTE, requerida=True, minimo=0),
    _importe("SALDO ATRASADO"),
    _importe("SALDO REQUERIDO"),
    _importe("PAGO_NORMAL"),
    Columna("PRODUCTO", Tipo.CATALOGO, requerida=True, catalogo=PRODUCTOS),
    _texto("ESTRATEGIA"),
    Columna("FECHA_ULTIMO_PAGO", Tipo.FECHA),
    _importe("IMP_ULTIMO_PAGO"),
    _texto("CALLE_EMPLEO"),
    _texto("NUM_EXT_EMPLEO"),
    _texto("NUM_INT_EMPLEO"),
    _texto("COLONIA_EMPLEO"),
    _texto("POBLACION_EMPLEO"),
    _texto("ESTADO_EMPLEO"),
    _texto("NOMBRE_AVAL"),
    Columna("TEL_AVAL", Tipo.CODIGO, patron=TELEFONO),
    _texto("CALLE_AVAL"),
    _texto("NUM_EXT_AVAL"),
    _texto("COLONIA_AVAL"),
    Columna("CP_AVAL", Tipo.CODIGO, patron=CODIGO_POSTAL),
    _texto("POBLACION_AVAL"),
    _texto("ESTADO_AVAL"),
    _texto("FIDIAPAGO"),
    Columna("TELEFONO1", Tipo.CODIGO, patron=TELEFONO),
    Columna("TELEFONO2", Tipo.CODIGO, patron=TELEFONO),
    Columna("TELEFONO3", Tipo.CODIGO, patron=TELEFONO),
    Columna("TELEFONO4", Tipo.CODIGO, patron=TELEFONO),
    _texto("TIPOTEL1"),
    _texto("TIPOTEL2"),
    _texto("TIPOTEL3"),
    _texto("TIPOTEL4"),
    Columna("LATITUD", Tipo.DECIMAL, minimo=-90, maximo=90),
    Columna("LONGITUD", Tipo.DECIMAL, minimo=-180, maximo=180),
    _texto("DESPACHO_GESTIONO"),
    _texto("ULTIMA_GESTION"),
    _texto("GESTION_DESC"),
    _texto("CAMPANIA_RELAMPAGO"),
    _texto("CAMPANIA"),
    _texto("PREVENTA"),
    _texto("ID_GRUPO"),
    _texto("GRUPO_MAZ"),
    _texto("CLAVE_SPEI"),
    _contador("PAGOS_CLIENTE"),
    _importe("MONTO_PAGOS"),
    _texto("GESTORES"),
    _texto("FOLIO_PLAN"),
    _texto("SEGMENTO_GENERACION"),
    _texto("ESTATUS_PLAN"),
    _contador("SEMANAS_ATRASO"),
    _texto("ATRASO"),
    _texto("GENERACION_PLAN"),
    _texto("CANCELACION_CUMPLIMIENTO_PLAN"),
    _texto("ULTIMO_ESTATUS"),
    _texto("EMPLEADO"),
    Columna("CANAL", Tipo.CATALOGO, requerida=True, catalogo=CANALES),
    _importe("ABONO_SEMANAL"),
    _contador("PLAZO"),
    _importe("MONTO_ABONADO"),
    _importe("MONTO_PLAN"),
    _importe("ENGANCHE"),
    _contador("PAGOS_RECIBIDOS"),
    _importe("SALDO_ANTES_DEL_PLAN"),
    _importe("SALDO_ATRASADO_ANTES_PLAN"),
    _importe("MORATORIOS_ANTES_PLAN"),
    _texto("ESTATUS_PROMESA_PAGO"),
    _importe("MONTO_PROMESA_PAGO"),
)

CONTRATO_V2 = ContratoFuente(VERSION_CONTRATO_V2, COLUMNAS, llave_unica="CLIENTE_UNICO")

HOJA_CARTERA = "CARTERA"
"""La hoja que se busca en un xlsx. Es la tabla autoritativa: la unica que se juzga y se publica."""

# --- CARRIER: la hoja companera -------------------------------------------------------------------

HOJA_CARRIER = "CARRIER"
"""La hoja companera observada junto a CARTERA. Se reconoce y se audita, pero no se publica ni
gobierna la publicacion de CARTERA."""

TELEFONOS_ANCHOS = (
    "TEL_AVAL",
    "TELEFONO1",
    "TELEFONO2",
    "TELEFONO3",
    "TELEFONO4",
    "TIPOTEL1",
    "TIPOTEL2",
    "TIPOTEL3",
    "TIPOTEL4",
)
"""Las 9 columnas de telefonos de CARTERA que CARRIER reemplaza por una sola, TELEFONO."""


def _columnas_carrier() -> tuple[str, ...]:
    """Las de CARTERA sin sus 9 columnas de telefonos, con TELEFONO donde estaba TELEFONO1: 85.
    El orden no es parte de la estructura que se reconoce; es el que escribe el generador."""
    columnas: list[str] = []
    for nombre in CONTRATO_V2.nombres:
        if nombre == "TELEFONO1":
            columnas.append("TELEFONO")
        if nombre not in TELEFONOS_ANCHOS:
            columnas.append(nombre)
    return tuple(columnas)


COLUMNAS_CARRIER = _columnas_carrier()

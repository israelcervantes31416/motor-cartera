"""pagos/v1: los movimientos economicos tal como los reporta el acreedor, con sus 23 columnas.

Una fila es UN MOVIMIENTO ECONOMICO: un pago recibido, con su fecha, su importe y lo que el acreedor
sabia de la cuenta en ese momento. Un mismo Cliente_Unico aparece tantas veces como movimientos
tenga, y eso es valido.

- Estructura exacta: las 23 columnas, ninguna mas (ver `contratos.fuente`). La columna tecnica que
  agregaba el sistema anterior con el nombre del archivo no es de la fuente: no esta aqui, y un
  archivo que la traiga tiene una columna de mas. Tampoco hay identificador de movimiento, de
  credito, de lote ni de fila: lo tecnico lo agrega el sistema, fuera de la fuente.
- No se deduplica nada. Dos filas identicas son dos filas, y las dos quedan en el dataset
  conformado. La regla historica que trataba como el mismo pago dos filas con el mismo cliente, la
  misma fecha de recepcion al segundo y el mismo importe recuperado a centavos queda documentada
  como referencia (LLAVE_HISTORICA), pero no se aplica: la deduplicacion, la conciliacion y los
  reversos son de un motor de pagos posterior, que tiene que poder explicar que movimiento fuente
  produjo cada movimiento canonico.
- El importe recuperado es el del movimiento y es obligatorio. Puede ser negativo, como un ajuste:
  interpretar el signo es de ese motor posterior, no de la fuente.
- Las fechas son hora local del acreedor, sin zona horaria. El anio y la semana se conservan como
  vienen: que correspondan a la fecha de recepcion no se exige todavia.

Los nombres de las columnas son los del contrato fuente que se simula, tal cual, con sus acentos:
son datos, y estan solo en las literales.
"""

from __future__ import annotations

from motor_cartera.contratos.cartera_v2 import CLIENTE
from motor_cartera.contratos.fuente import Columna, ContratoFuente, Tipo

VERSION_CONTRATO_PAGOS = "pagos/v1"
"""Cambia si cambian las columnas, sus reglas o la forma canonica con que se firma el contenido."""

COLUMNAS: tuple[Columna, ...] = (
    Columna("Año", Tipo.ENTERO, minimo=1900, maximo=2100),
    Columna("Semana", Tipo.ENTERO, minimo=1, maximo=53),
    Columna("Territorio"),
    Columna("Zona"),
    Columna("Cliente_Unico", Tipo.CODIGO, requerida=True, patron=CLIENTE),
    Columna("Fecha_Recepción", Tipo.FECHA_HORA, requerida=True),
    Columna("Segmento"),
    Columna("Gerencia"),
    Columna("Tipo_Cartera"),
    Columna("Producto"),
    Columna("Campaña"),
    Columna("Gestor"),
    Columna("Días_de_Atraso", Tipo.ENTERO, minimo=0),
    Columna("Semanas_de_Atraso", Tipo.ENTERO, minimo=0),
    Columna("Plan_de_Pago"),
    Columna("Fecha_de_Gestion", Tipo.FECHA_HORA),
    Columna("Recuperación_por_Gestión", Tipo.IMPORTE, requerida=True),
    Columna("Concepto_Cálculo"),
    Columna("Cargos_Automáticos", Tipo.IMPORTE),
    Columna("Captación", Tipo.IMPORTE),
    Columna("Cobranza_Total", Tipo.IMPORTE),
    Columna("Porcentaje_Comision", Tipo.DECIMAL),
    Columna("Monto_Comision", Tipo.IMPORTE),
)

CONTRATO_PAGOS = ContratoFuente(VERSION_CONTRATO_PAGOS, COLUMNAS, llave_unica=None)

LLAVE_HISTORICA = ("Cliente_Unico", "Fecha_Recepción", "Recuperación_por_Gestión")
"""La llave con que el sistema anterior trataba dos filas como el mismo pago: el cliente, la fecha
de recepcion truncada al segundo y el importe a centavos. Es una referencia para el motor de pagos
posterior; v0.6 no la aplica y conserva cada fila."""

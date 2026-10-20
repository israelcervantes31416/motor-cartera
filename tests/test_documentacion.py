"""Los diccionarios de datos dicen exactamente lo que dicen los contratos: si un contrato cambia una
columna, su diccionario no se queda atrás sin que una prueba lo note."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from motor_cartera import __version__
from motor_cartera.atribucion.reglas import Clasificacion as ClasificacionAtribucion
from motor_cartera.atribucion.reglas import Motivo as MotivoAtribucion
from motor_cartera.contratos.cartera_v2 import COLUMNAS_CARRIER, CONTRATO_V2, TELEFONOS_ANCHOS
from motor_cartera.contratos.pagos import CONTRATO_PAGOS
from motor_cartera.db.modelos import ResultadoEjecucionDecisionV2
from motor_cartera.decision_v2.features import FEATURES
from motor_cartera.decision_v2.politica import CodigoMotivo, hash_politica
from motor_cartera.evaluacion.reglas import EstadoEvaluacion
from motor_cartera.evaluacion.reglas import Motivo as MotivoEvaluacion
from motor_cartera.lifecycle.reglas import TipoEvento
from motor_cartera.motor_pagos.reglas import Clasificacion, Motivo

DOCS = Path(__file__).resolve().parents[1] / "docs"
FILA = re.compile(r"^\| (\d+) \| `([^`]+)` \| ([^|]+) \| ([^|]+) \|")


def _filas(archivo: str) -> list[tuple[int, str, str, str]]:
    filas = []
    for linea in (DOCS / archivo).read_text(encoding="utf-8").splitlines():
        if coincidencia := FILA.match(linea):
            numero, nombre, tipo, requerida = coincidencia.groups()
            filas.append((int(numero), nombre, tipo.strip(), requerida.strip()))
    return filas


@pytest.mark.parametrize(
    ("archivo", "contrato"),
    [("diccionario_cartera.md", CONTRATO_V2), ("diccionario_pagos.md", CONTRATO_PAGOS)],
    ids=["cartera-v2", "pagos-v1"],
)
def test_cada_diccionario_lista_las_columnas_de_su_contrato_en_su_orden(archivo, contrato):
    filas = _filas(archivo)

    assert [numero for numero, *_ in filas] == list(range(1, len(contrato.columnas) + 1))
    assert tuple(nombre for _, nombre, *_ in filas) == contrato.nombres
    requeridas = {nombre for _, nombre, _, requerida in filas if requerida == "sí"}
    assert requeridas == {c.nombre for c in contrato.columnas if c.requerida}


def test_el_documento_de_carrier_dice_que_columnas_reemplaza():
    texto = (DOCS / "carrier.md").read_text(encoding="utf-8")

    assert (
        f"**{len(COLUMNAS_CARRIER)}\ncolumnas**" in texto
        or f"{len(COLUMNAS_CARRIER)} columnas" in texto
    )
    assert len(COLUMNAS_CARRIER) == 85
    for columna in ("TEL_AVAL", "TELEFONO1", "TIPOTEL1", "TELEFONO"):
        assert f"`{columna}`" in texto
    assert len(TELEFONOS_ANCHOS) == 9


def test_el_documento_del_motor_de_pagos_define_cada_clasificacion_y_cada_motivo():
    """Cada clase y cada motivo que el motor publica tiene su fila en las tablas de
    motor_pagos.md: uno nuevo no llega a la API sin que se documente que quiere decir."""
    texto = (DOCS / "motor_pagos.md").read_text(encoding="utf-8")
    filas = {
        linea.split(" | ")[0].strip("| `")
        for linea in texto.splitlines()
        if linea.startswith("| `")
    }

    assert {c.value for c in Clasificacion} <= filas
    assert {m.value for m in Motivo} <= filas


def _codigos_de_tablas(archivo: str) -> set[str]:
    """Lo que nombra la primera columna de cada fila de una tabla del documento: `CODIGO`."""
    texto = (DOCS / archivo).read_text(encoding="utf-8")
    return {
        linea.split(" | ")[0].strip("| `")
        for linea in texto.splitlines()
        if linea.startswith("| `")
    }


def test_el_documento_de_la_atribucion_define_cada_clasificacion_y_cada_motivo():
    """Cada clase y cada motivo de atribucion/v1 tiene su fila en atribucion.md."""
    filas = _codigos_de_tablas("atribucion.md")

    assert {c.value for c in ClasificacionAtribucion} <= filas
    assert {m.value for m in MotivoAtribucion} <= filas


def test_el_documento_del_lifecycle_define_cada_tipo_de_evento_y_cada_estado_de_una_promesa():
    """Cada tipo de evento de lifecycle/v1, y cada estado y cada motivo de evaluacion-promesa/v1,
    tiene su fila en lifecycle.md."""
    filas = _codigos_de_tablas("lifecycle.md")

    assert {t.value for t in TipoEvento} <= filas
    assert {e.value for e in EstadoEvaluacion} <= filas
    assert {m.value for m in MotivoEvaluacion} <= filas


def test_la_documentacion_no_atribuye_causalidad():
    """Asociacion operacional no es causalidad: ningun documento del lifecycle, de la atribucion o
    de decision/v2 dice que una gestion produjo o causo un pago, salvo para negarlo."""
    for archivo in ("lifecycle.md", "atribucion.md", "decision_v2.md"):
        texto = (DOCS / archivo).read_text(encoding="utf-8").lower()
        for frase in ("causado por", "causó el pago", "produjo el pago"):
            assert frase not in texto, (archivo, frase)
        assert "no causalidad" in texto or "no es causalidad" in texto


DICCIONARIO = re.compile(
    r"^\| (\d+) \| `([^`]+)` \| ([A-Z]+) \| ([A-Z]+) \| [^|]+ \| [^|]+ \| ([^|]+) \|$"
)


def test_el_diccionario_de_decision_v2_es_el_de_features_decision_v1():
    """Cada feature de features-decision/v1 tiene su fila en el diccionario de decision_v2.md, en su
    orden canonico, con su tipo, su familia y si puede ser NULL: una feature nueva, o una que
    cambia, no llega a la API sin que se documente que quiere decir."""
    texto = (DOCS / "decision_v2.md").read_text(encoding="utf-8")
    seccion = texto.split("## El diccionario de features", 1)[1].split("\n## ", 1)[0]
    filas = [c.groups() for linea in seccion.splitlines() if (c := DICCIONARIO.match(linea))]

    assert [int(numero) for numero, *_ in filas] == list(range(1, len(FEATURES) + 1))
    assert [(nombre, tipo, familia) for _, nombre, tipo, familia, _ in filas] == [
        (f.nombre, f.tipo.value, f.familia.value) for f in FEATURES
    ]
    assert [not nulo.strip().startswith("nunca") for *_, nulo in filas] == [
        f.nula for f in FEATURES
    ]


def test_el_documento_de_decision_v2_define_cada_motivo_y_cada_resultado():
    """Cada codigo de motivo y cada resultado de una ejecucion tiene su fila en decision_v2.md, y el
    hash de la politica que documenta es el de la politica que aplica el servicio."""
    filas = _codigos_de_tablas("decision_v2.md")
    texto = (DOCS / "decision_v2.md").read_text(encoding="utf-8")

    assert {c.value for c in CodigoMotivo} <= filas
    assert {r.value for r in ResultadoEjecucionDecisionV2} <= filas
    assert hash_politica() in texto


def test_la_documentacion_de_decision_v2_no_la_presenta_como_prediccion_ni_como_politica_real():
    texto = " ".join((DOCS / "decision_v2.md").read_text(encoding="utf-8").split())

    assert (
        "Esta política demuestra la capacidad del motor; no representa una política real de un "
        "acreedor." in texto
    )
    assert "No es una predicción." in texto
    for frase in ("predice que", "la predicción de", "probabilidad de que"):
        assert frase not in texto.lower(), frase


def test_el_documento_de_la_geografia_define_cada_estado_motivo_y_resultado():
    """Cada estado, motivo, comparacion y resultado de geografia/v1, y cada estado de una version de
    la referencia, tiene su fila en geografia.md; y la referencia oficial que documenta es la del
    manifiesto, con su SHA-256."""
    from motor_cartera.db.modelos import EstadoVersionGeografia, ResultadoEjecucionGeografia
    from motor_cartera.geografia import reglas
    from motor_cartera.geografia.referencia import referencias

    filas = _codigos_de_tablas("geografia.md")
    texto = (DOCS / "geografia.md").read_text(encoding="utf-8")

    for vocabulario in (
        reglas.EstadoGeografia,
        reglas.Motivo,
        reglas.Comparacion,
        ResultadoEjecucionGeografia,
        EstadoVersionGeografia,
    ):
        assert {v.value for v in vocabulario} <= filas, vocabulario.__name__
    inegi = referencias()["inegi-mg-2025-municipal"]
    assert inegi.sha256 in texto
    assert inegi.datos["url"] in texto
    for regla in ("ST_MakePoint(longitud, latitud)", "ST_Covers", "EPSG:4326"):
        assert regla in texto


def test_el_documento_de_territorial_v2_define_cada_estado_motivo_y_resultado():
    """Cada estado, motivo y resultado de territorial/v2 tiene su fila en territorial_v2.md, y la
    politica que documenta, con su tolerancia y su hash, es la que aplica el servicio."""
    from motor_cartera.db.modelos import ResultadoEjecucionTerritorialV2
    from motor_cartera.territorial_v2.politica import (
        PUBLIC_V2,
        EstadoAsignacion,
        MotivoAsignacion,
    )
    from motor_cartera.territorial_v2.politica import hash_politica as hash_territorial

    filas = _codigos_de_tablas("territorial_v2.md")
    texto = (DOCS / "territorial_v2.md").read_text(encoding="utf-8")

    for vocabulario in (EstadoAsignacion, MotivoAsignacion, ResultadoEjecucionTerritorialV2):
        assert {v.value for v in vocabulario} <= filas, vocabulario.__name__
    assert hash_territorial() in texto
    assert f"{PUBLIC_V2.tolerancia_empate_m} m" in texto
    assert "pertenencia territorial, no capacidad" in texto


def test_el_documento_de_las_bases_define_cada_campo_de_una_base():
    from motor_cartera.bases.configuracion import LLAVES_DE_UNA_BASE, VERSION_CONFIGURACION

    texto = (DOCS / "bases_operativas.md").read_text(encoding="utf-8")
    campos = {
        nombre.strip()
        for linea in texto.splitlines()
        if linea.startswith("| `")
        for nombre in linea.split(" | ")[0].strip("| ").replace("`", "").split(",")
    }

    assert set(LLAVES_DE_UNA_BASE) <= campos
    assert VERSION_CONFIGURACION in texto
    assert "La cobertura se declara, nunca se infiere." in texto


def test_ninguna_documentacion_de_la_geografia_promete_geocodificar_ni_corregir():
    for archivo in ("geografia.md", "territorial_v2.md", "bases_operativas.md"):
        texto = " ".join((DOCS / archivo).read_text(encoding="utf-8").lower().split())
        for frase in ("se geocodifica", "geocodificamos", "se corrige la coordenada"):
            assert frase not in texto, (archivo, frase)
    texto = " ".join((DOCS / "geografia.md").read_text(encoding="utf-8").split())
    assert "No geocodifica direcciones." in texto
    assert "No corrige coordenadas." in texto


def _campos_de_tablas(archivo: str) -> set[str]:
    """Los nombres de la primera columna de cada fila de una tabla: `campo` o `a`, `b`."""
    texto = (DOCS / archivo).read_text(encoding="utf-8")
    return {
        nombre.strip()
        for linea in texto.splitlines()
        if linea.startswith("| `")
        for nombre in linea.split(" | ")[0].strip("| ").replace("`", "").split(",")
    }


def test_el_documento_de_campo_v2_define_cada_estado_motivo_jornada_nivel_y_resultado():
    """Cada estado de un plan y de una asignacion, cada motivo, cada estado de jornada, cada nivel
    de la particion y cada resultado de campo/v2 tiene su fila en campo_v2.md, y la politica que
    documenta, con su hash y su cuadricula, es la que aplica el servicio."""
    from motor_cartera.campo_v2.politica import (
        PUBLIC_V2,
        EstadoCampo,
        EstadoJornada,
        MotivoCampo,
        NivelUnidad,
    )
    from motor_cartera.campo_v2.politica import hash_politica as hash_campo
    from motor_cartera.db.modelos import EstadoCampoV2, ResultadoEjecucionCampoV2

    filas = _codigos_de_tablas("campo_v2.md")
    texto = " ".join((DOCS / "campo_v2.md").read_text(encoding="utf-8").split())

    for vocabulario in (
        EstadoCampoV2,
        EstadoCampo,
        MotivoCampo,
        EstadoJornada,
        NivelUnidad,
        ResultadoEjecucionCampoV2,
    ):
        assert {v.value for v in vocabulario} <= filas, vocabulario.__name__
    assert hash_campo() in texto
    assert f"EPSG:{PUBLIC_V2.srid_metrico}" in texto
    assert f"{PUBLIC_V2.tamano_tramo_m:,} m" in texto
    assert f"{PUBLIC_V2.tamano_microtramo_m:,} m" in texto
    assert "capacidad declarada, no factibilidad vial" in texto.lower()


def test_el_documento_de_la_configuracion_de_campo_define_cada_campo_y_cada_excepcion():
    from motor_cartera.campo_v2.configuracion import (
        LLAVES_DE_UN_CALENDARIO,
        LLAVES_DE_UN_GESTOR,
        LLAVES_DE_UNA_EXCEPCION,
        LLAVES_DEL_DOCUMENTO,
        VERSION_CONFIGURACION,
        TipoExcepcion,
    )
    from motor_cartera.db.modelos import EstadoVersionCampo

    texto = (DOCS / "configuracion_campo.md").read_text(encoding="utf-8")
    campos = _campos_de_tablas("configuracion_campo.md")

    for llaves in (
        LLAVES_DEL_DOCUMENTO,
        LLAVES_DE_UN_CALENDARIO,
        LLAVES_DE_UNA_EXCEPCION,
        LLAVES_DE_UN_GESTOR,
    ):
        assert set(llaves) <= campos, sorted(set(llaves) - campos)
    assert {t.value for t in TipoExcepcion} <= campos
    assert {e.value for e in EstadoVersionCampo} <= campos
    assert VERSION_CONFIGURACION in texto
    assert "Un gestor es un código, nunca una persona." in texto


def test_el_documento_de_la_particion_define_cada_nivel_su_cuadricula_y_sus_identificadores():
    from motor_cartera.campo_v2.politica import ESPACIO_CAMPO, PUBLIC_V2, NivelUnidad, particion

    filas = _codigos_de_tablas("particion_campo.md")
    texto = (DOCS / "particion_campo.md").read_text(encoding="utf-8")

    assert {n.value for n in NivelUnidad} <= filas
    assert f"EPSG:{PUBLIC_V2.srid_metrico}" in texto
    assert f"{PUBLIC_V2.tamano_tramo_m:,} m" in texto
    assert f"{PUBLIC_V2.tamano_microtramo_m:,} m" in texto
    assert "k = floor(round(numeric(x), 3) / T)" in texto
    # El ejemplo del documento es el identificador que calcula la politica.
    assert str(ESPACIO_CAMPO) in texto
    assert '"campo-public-v2:MICROTRAMO:BASE_A:21114:2897:787"' in texto
    assert str(particion("BASE_A", "21114", 2897, 787).microtramo_id) in texto


def test_ninguna_documentacion_de_campo_promete_una_ruta_ni_publica_una_coordenada():
    for archivo in ("campo_v2.md", "configuracion_campo.md", "particion_campo.md"):
        texto = " ".join((DOCS / archivo).read_text(encoding="utf-8").lower().split())
        for frase in (
            "ruta óptima",
            "ruta más corta",
            "tiempo estimado de llegada",
            "orden óptimo",
        ):
            assert frase not in texto, (archivo, frase)
    texto = " ".join((DOCS / "campo_v2.md").read_text(encoding="utf-8").split())
    assert "No usa distancias, tiempos de traslado, rutas ni la duración de una visita" in texto
    assert "Ninguna respuesta lleva una coordenada, el índice de una celda, un recuadro" in texto


def test_el_documento_de_ruteo_v2_define_cada_resultado_y_dice_que_es_una_heuristica():
    """Cada resultado de una ejecucion de ruteo/v2 tiene su fila en ruteo_v2.md, cada estado se
    nombra, y el documento dice lo que la ruta no es: un optimo, ni una hora con trafico."""
    from motor_cartera.db.modelos import EstadoRuteoV2, ResultadoEjecucionRuteoV2

    filas = _codigos_de_tablas("ruteo_v2.md")
    texto = " ".join((DOCS / "ruteo_v2.md").read_text(encoding="utf-8").split())

    assert {r.value for r in ResultadoEjecucionRuteoV2} <= filas
    assert {e.value for e in EstadoRuteoV2} <= set(re.findall(r"`([A-Z_]+)`", texto))
    assert "heurística determinista, no una ruta óptima" in texto
    assert "sin tráfico" in texto


def test_el_documento_de_la_politica_de_ruteo_define_cada_estado_motivo_y_parametro():
    """Cada estado de una ruta y de una parada tiene su fila en politica_ruteo.md, cada motivo se
    nombra, y la politica que documenta, con su hash y sus parametros, es la que aplica el
    servicio."""
    from motor_cartera.ruteo_v2.politica import (
        ALGORITMO,
        PUBLIC_V2,
        EstadoParada,
        EstadoRuta,
        MotivoParada,
        hash_politica,
    )

    filas = _codigos_de_tablas("politica_ruteo.md")
    texto = (DOCS / "politica_ruteo.md").read_text(encoding="utf-8")

    assert {e.value for e in EstadoRuta} <= filas
    assert {e.value for e in EstadoParada} <= filas
    for motivo in MotivoParada:
        assert f"`{motivo.value}`" in texto, motivo
    assert hash_politica(PUBLIC_V2) in texto
    assert ALGORITMO in texto
    for parametro in ("max_snap_distancia_m", "duracion_servicio_s", "bloque_matriz"):
        assert f"| `{parametro}` | `{getattr(PUBLIC_V2, parametro)}` |" in texto
    assert f"| `max_mejoras_2opt` | `{PUBLIC_V2.max_mejoras_2opt}` |" in texto


def test_el_documento_de_la_red_vial_fija_la_imagen_los_comandos_y_la_atribucion():
    """La imagen de OSRM, su version, los comandos de la preparacion, el prefijo con que el backend
    declara su red, los estados de una version y la atribucion de OpenStreetMap que documenta
    red_vial.md son los del codigo."""
    from motor_cartera.api.esquemas_ruteo_v2 import ATRIBUCION_OSM
    from motor_cartera.db.modelos import EstadoVersionRedVial
    from motor_cartera.ruteo_v2 import red_vial
    from motor_cartera.ruteo_v2.proveedor import PREFIJO_DATA_VERSION

    texto = (DOCS / "red_vial.md").read_text(encoding="utf-8")

    assert red_vial.IMAGEN_OSRM in texto
    assert red_vial.VERSION_OSRM in texto
    assert ATRIBUCION_OSM in " ".join(texto.split())
    assert PREFIJO_DATA_VERSION in texto
    for comando in red_vial.comandos("<archivo>", "<huella>"):
        assert " ".join(comando) in texto, comando
    for estado in EstadoVersionRedVial:
        assert f"`{estado.value}`" in texto, estado
    assert ":latest" not in texto
    assert "Nunca se usa el servidor público de OSRM" in " ".join(texto.split())


def test_ninguna_documentacion_de_ruteo_promete_el_optimo_ni_publica_una_coordenada():
    for archivo in ("ruteo_v2.md", "politica_ruteo.md", "red_vial.md"):
        texto = " ".join((DOCS / archivo).read_text(encoding="utf-8").lower().split())
        for frase in (
            "la ruta óptima",
            "la ruta más corta",
            "tráfico en tiempo real",
            "garantiza la llegada",
        ):
            assert frase not in texto, (archivo, frase)
    texto = " ".join((DOCS / "ruteo_v2.md").read_text(encoding="utf-8").split())
    assert "no publica coordenadas, geometrías ni polilíneas" in texto


def test_la_base_de_ruteo_va_unida_a_su_opcion():
    # En Mexico la longitud de una base es negativa. Separada de su opcion, el argparse de Python
    # 3.12 la toma por otra opcion, y la prueba de humo termina con "expected one argument".
    raiz = DOCS.parent
    separada = re.compile(r"--base-ruteo\s+[\"'<$\d-]")
    for archivo in (".github/workflows/ci.yml", "README.md", "scripts/prueba_de_humo.py"):
        assert not separada.search((raiz / archivo).read_text(encoding="utf-8")), archivo
    ci = (raiz / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert '--base-ruteo="$base"' in ci


# --- analytics/v1 ---------------------------------------------------------------------------------

METRICA_DOCUMENTADA = re.compile(r"^\| `([a-z_]+\.[a-z_]+)` \|", re.MULTILINE)


def test_cada_metrica_del_catalogo_esta_documentada_una_vez_y_ninguna_mas():
    """Cada metrica del catalogo tiene exactamente una fila en metricas_analytics.md, y ninguna fila
    documenta una metrica que el catalogo no tiene. Los codigos del catalogo son unicos."""
    from motor_cartera.analytics.catalogo import METRICAS

    codigos = [m.codigo for m in METRICAS]
    documentadas = METRICA_DOCUMENTADA.findall(
        (DOCS / "metricas_analytics.md").read_text(encoding="utf-8")
    )

    assert len(codigos) == len(set(codigos))
    assert sorted(documentadas) == sorted(codigos)


def test_el_documento_de_analytics_define_cada_resultado_estado_motivo_y_error():
    """Cada resultado de una ejecucion, cada estado de un valor, cada motivo de NO_DISPONIBLE y
    cada error del manifiesto tiene su fila en analytics.md; los puntos de falla se nombran."""
    from motor_cartera.analytics import cobertura, ejecuciones, manifiesto, supresion
    from motor_cartera.db.modelos import ResultadoEjecucionAnalytics

    filas = _codigos_de_tablas("analytics.md")
    texto = (DOCS / "analytics.md").read_text(encoding="utf-8")
    errores = {
        clase.codigo
        for clase in vars(manifiesto).values()
        if isinstance(clase, type)
        and issubclass(clase, manifiesto.EntradaNoAnalizable)
        and clase is not manifiesto.EntradaNoAnalizable
    }

    assert {r.value for r in ResultadoEjecucionAnalytics} | {"CERRADA_AL_BAJAR"} <= filas
    assert {"DISPONIBLE", "NO_DISPONIBLE", "SUPRIMIDO", "NO_APLICA"} <= filas
    assert {
        cobertura.ENTRADA_NO_DECLARADA,
        cobertura.PAGOS_SIN_COBERTURA,
        cobertura.INTERPRETACION_INCOMPLETA,
        cobertura.ATRIBUCION_NO_DECLARADA,
    } <= filas
    assert len(errores) == 24 and errores <= filas
    for codigo in (supresion.PRIMARIA, supresion.COMPLEMENTARIA, "DENOMINADOR_CERO"):
        assert f"`{codigo}`" in texto, codigo
    for punto in ejecuciones.PUNTOS_DE_FALLA:
        assert f"`{punto}`" in texto, punto


def test_las_versiones_y_los_hashes_documentados_son_los_del_servicio():
    from motor_cartera.analytics.catalogo import VERSION_CATALOGO, VERSION_MOTOR, hash_catalogo
    from motor_cartera.analytics.politica import PUBLIC_V1
    from motor_cartera.analytics.politica import hash_politica as hash_de_la_politica

    analytics = (DOCS / "analytics.md").read_text(encoding="utf-8")
    metricas = (DOCS / "metricas_analytics.md").read_text(encoding="utf-8")

    for valor in (VERSION_MOTOR, VERSION_CATALOGO, PUBLIC_V1.version, hash_catalogo()):
        assert valor in analytics, valor
    assert hash_de_la_politica(PUBLIC_V1) in analytics
    assert hash_catalogo() in metricas


def test_el_documento_de_cohortes_define_cada_conjunto_con_sus_llaves():
    """Cada conjunto de dimensiones del catalogo tiene su fila en cohortes_analytics.md, con sus
    llaves; y cada llave de dimension se explica."""
    from motor_cartera.analytics.catalogo import CONJUNTOS, Conjunto

    texto = (DOCS / "cohortes_analytics.md").read_text(encoding="utf-8")
    filas = {
        linea.split(" | ")[0].strip("| `"): linea
        for linea in texto.splitlines()
        if linea.startswith("| `")
    }

    for conjunto in Conjunto:
        fila = filas[conjunto.value]
        for llave in CONJUNTOS[conjunto].claves:
            assert f"`{llave}`" in fila, (conjunto, llave)
    for llave in {c for d in CONJUNTOS.values() for c in d.claves}:
        assert llave in filas, llave
    for etapa in ("COHORTE", "GESTIONADAS", "CONTACTADAS", "CON_PROMESA", "CANDIDATAS"):
        assert f"`{etapa}`" in texto, etapa


def test_la_documentacion_de_analytics_no_le_atribuye_causalidad_a_nada():
    """Describir no es probar causalidad: ningun documento de analytics/v1 dice que una gestion
    causo, genero o incremento un pago, y el principal lo niega con todas sus letras."""
    for archivo in ("analytics.md", "metricas_analytics.md", "cohortes_analytics.md"):
        texto = " ".join((DOCS / archivo).read_text(encoding="utf-8").lower().split())
        for frase in (
            "causó el pago",
            "causado por",
            "generó la recuperación",
            "recuperación generada por",
            "incrementó la recuperación",
            "impacto de la gestión",
        ):
            assert frase not in texto, (archivo, frase)
    principal = " ".join((DOCS / "analytics.md").read_text(encoding="utf-8").split())
    assert "no prueba causalidad, no predice y no recomienda" in principal
    assert "asociación operacional, no causalidad" in principal


def test_las_decisiones_estan_todas_y_en_orden():
    texto = (DOCS / "decisiones.md").read_text(encoding="utf-8")
    numeros = [int(n) for n in re.findall(r"^## (\d+)\. ", texto, re.MULTILINE)]
    indice = [int(n) for n in re.findall(r"^(\d+)\. \[", texto, re.MULTILINE)]

    assert numeros == list(range(1, 304))
    assert indice == numeros
    introduccion = " ".join(texto[:5000].split())
    assert "las 175 a 196, de Collection Analytics" in introduccion
    assert "las 197 a 222, de Predictive Intelligence" in introduccion
    assert "las 223 a 247, de la Optimización Matemática" in introduccion
    assert "las 248 a 274, de la App + Dashboards" in introduccion
    assert "las 275 a 303, de Cloud, Observabilidad y Hardening" in introduccion


def test_el_readme_marca_la_version_estable_y_la_que_sigue():
    texto = " ".join((DOCS.parent / "README.md").read_text(encoding="utf-8").split())

    assert f"**Versión estable: v{__version__}**" in texto
    assert "➡️ v1.0.0 — Collection Intelligence Platform" in texto
    assert "[CHANGELOG](CHANGELOG.md)" in texto
    # El compose de desarrollo sigue sin usuarios, y el README lo dice tal cual.
    assert "Desarrollo local sin usuarios (clave de servicio del proxy)" in texto


VERSION_DEL_CHANGELOG = re.compile(
    r"^## \[(v\d+\.\d+\.\d+)\] — .+ — (\d{4}-\d{2}-\d{2})$", re.MULTILINE
)


def test_el_changelog_tiene_cada_version_con_su_release():
    """De la versión del paquete a v0.1.0, en orden, cada una con su fecha y su enlace a la release:
    una versión nueva no se publica sin su entrada."""
    texto = (DOCS.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    versiones = [v for v, _ in VERSION_DEL_CHANGELOG.findall(texto)]

    assert "## [Unreleased]" in texto
    assert versiones[0] == f"v{__version__}"
    assert versiones[-1] == "v0.1.0"
    clave = [tuple(int(n) for n in v[1:].split(".")) for v in versiones]
    assert clave == sorted(clave, reverse=True)
    assert len(set(versiones)) == len(versiones)
    for v in versiones:
        enlace = f"[{v}]: https://github.com/israelcervantes31416/motor-cartera/releases/tag/{v}"
        assert enlace in texto, v


# --- predictive/model-v1 --------------------------------------------------------------------------

DOCUMENTOS_PREDICTIVOS = (
    "predictivo.md",
    "features_predictivas.md",
    "modelos_predictivos.md",
    "backtesting_predictivo.md",
)


def test_el_documento_predictivo_define_cada_resultado_error_motivo_y_punto_de_falla():
    """Cada resultado de un entrenamiento y de un scoring, cada error del manifiesto, del scoring y
    de la explicacion, cada motivo y estado de una prediccion y cada madurez tiene su fila en
    predictivo.md; los puntos de falla se nombran."""
    from motor_cartera.db.modelos import (
        ResultadoEntrenamientoPredictivo,
        ResultadoScoringPredictivo,
    )
    from motor_cartera.predictivo import entrenamiento, explicacion, manifiesto, scoring
    from motor_cartera.predictivo.targets import Madurez

    filas = _codigos_de_tablas("predictivo.md")
    texto = (DOCS / "predictivo.md").read_text(encoding="utf-8")

    def codigos(modulo, base) -> set[str]:
        return {
            clase.codigo
            for clase in vars(modulo).values()
            if isinstance(clase, type) and issubclass(clase, base)
        }

    errores = codigos(manifiesto, manifiesto.ManifiestoInvalido)
    assert len(errores) == 25 and errores <= filas
    assert codigos(scoring, scoring.ScoringInvalido) - {"SCORING_INVALIDO"} <= filas
    assert (
        codigos(explicacion, explicacion.ExplicacionNoDisponible) - {"EXPLICACION_NO_DISPONIBLE"}
        <= filas
    )
    assert {r.value for r in ResultadoEntrenamientoPredictivo} | {"CERRADA_AL_BAJAR"} <= filas
    assert {r.value for r in ResultadoScoringPredictivo} <= filas
    assert {m.value for m in Madurez} <= filas
    assert {
        "COMPLETA",
        "CON_IMPUTACION",
        "DATO_NO_REPORTADO",
        "SIN_HISTORIA",
        "SIN_COBERTURA_PAGOS",
        "SIN_EVALUACION_PROMESAS",
        "CATEGORIA_FALTANTE",
        "CATEGORIA_DESCONOCIDA",
        "CANDIDATA",
        "VALIDADA",
        "PUBLICADA",
        "RECHAZADA",
    } <= filas
    for punto in (*entrenamiento.PUNTOS_DE_FALLA, *scoring.PUNTOS_DE_FALLA):
        assert f"`{punto}`" in texto, punto


def test_cada_feature_esta_documentada_una_vez_y_ninguna_mas():
    from motor_cartera.predictivo.features import FEATURES

    texto = (DOCS / "features_predictivas.md").read_text(encoding="utf-8")
    documentadas = re.findall(r"^\| `([a-z_0-9]+)` \| [A-Z]+ \|", texto, re.MULTILINE)
    assert documentadas == [f.nombre for f in FEATURES]


def test_las_versiones_y_los_hashes_predictivos_documentados_son_los_del_servicio():
    from motor_cartera.predictivo import features, targets
    from motor_cartera.predictivo.politica import (
        PUBLIC_V1,
        VERSION_ARTEFACTO,
        VERSION_MODELO,
        hash_politica,
    )

    predictivo = (DOCS / "predictivo.md").read_text(encoding="utf-8")
    for valor in (
        VERSION_MODELO,
        VERSION_ARTEFACTO,
        targets.VERSION_TARGETS,
        targets.hash_targets(),
        features.VERSION_FEATURES,
        features.hash_features(),
        PUBLIC_V1.version,
        hash_politica(PUBLIC_V1),
    ):
        assert valor in predictivo, valor
    assert features.hash_features() in (DOCS / "features_predictivas.md").read_text(
        encoding="utf-8"
    )
    assert hash_politica(PUBLIC_V1) in (DOCS / "modelos_predictivos.md").read_text(encoding="utf-8")


def test_la_documentacion_predictiva_no_promete_causalidad():
    """Predecir no es causar: ninguna documentacion de v0.15 habla de un impacto, un incremento
    causado o un tratamiento optimo, y uplift solo aparece para decir que no lo hay."""
    for archivo in DOCUMENTOS_PREDICTIVOS:
        texto = " ".join((DOCS / archivo).read_text(encoding="utf-8").lower().split())
        for frase in (
            "impacto causal",
            "incremento causado",
            "tratamiento óptimo",
            "tratamiento optimo",
            "causó el pago",
            "causado por",
        ):
            assert frase not in texto, (archivo, frase)
        for oracion in re.split(r"(?<=\.) ", texto):
            if "uplift" in oracion:
                assert "no hay" in oracion, (archivo, oracion)
    principal = " ".join((DOCS / "predictivo.md").read_text(encoding="utf-8").split())
    assert "Una predicción no es causal." in principal
    assert "no estiman causalidad" in principal.lower()


# --- optimization/v1 ------------------------------------------------------------------------------

DOCUMENTOS_DE_OPTIMIZACION = (
    "optimizacion.md",
    "modelo_optimizacion.md",
    "configuracion_optimizacion.md",
)


def test_el_documento_de_optimizacion_define_cada_estado_resultado_motivo_error_y_falla():
    """Cada estado y motivo de una cuenta, cada estado y resultado de una ejecucion, cada estado del
    solver, cada hallazgo del prechequeo y cada error de abrir tiene su fila en optimizacion.md; los
    puntos de falla se nombran."""
    from motor_cartera.db.modelos import EstadoOptimizacion, ResultadoOptimizacion
    from motor_cartera.optimizacion import ejecuciones
    from motor_cartera.optimizacion.politica import EstadoCuenta, EstadoSolver, Motivo

    filas = _codigos_de_tablas("optimizacion.md")
    texto = (DOCS / "optimizacion.md").read_text(encoding="utf-8")
    errores = {
        clase.codigo
        for clase in vars(ejecuciones).values()
        if isinstance(clase, type) and issubclass(clase, ejecuciones.OptimizacionInvalida)
    }
    assert len(errores) == 17 and errores <= filas
    assert {"OPTIMIZACION_EN_PROCESO", "OPTIMIZACION_YA_PUBLICADA"} <= filas
    assert {"CONFIGURACION_INVALIDA", "FILTRO_DESCONOCIDO", "OPTIMIZACION_NO_PUBLICADA"} <= filas
    assert {e.value for e in EstadoCuenta} <= filas
    assert {m.value for m in Motivo} <= filas
    assert {e.value for e in EstadoOptimizacion} <= filas
    assert {r.value for r in ResultadoOptimizacion} | {"CERRADA_AL_BAJAR"} <= filas
    assert {e.value for e in EstadoSolver} <= filas
    for punto in ejecuciones.PUNTOS_DE_FALLA:
        assert f"`{punto}`" in texto, punto


def test_cada_hallazgo_del_prechequeo_esta_documentado():
    """Los codigos que el codigo puede producir, leidos de su fuente, son los documentados."""
    fuente = "".join(
        (DOCS.parent / "src" / "motor_cartera" / "optimizacion" / f).read_text(encoding="utf-8")
        for f in ("formulacion.py", "ejecuciones.py")
    )
    producidos = set(re.findall(r'Hallazgo\(\s*"([A-Z_]+)"', fuente))
    assert len(producidos) == 11 and producidos <= _codigos_de_tablas("optimizacion.md")


def test_las_versiones_y_el_hash_de_optimizacion_documentados_son_los_del_servicio():
    from motor_cartera.optimizacion.configuracion import VERSION_CONFIGURACION
    from motor_cartera.optimizacion.politica import (
        ALGORITMO,
        PUBLIC_V1,
        VERSION_MOTOR,
        hash_politica,
    )

    texto = (DOCS / "optimizacion.md").read_text(encoding="utf-8")
    for valor in (
        VERSION_MOTOR,
        PUBLIC_V1.version,
        hash_politica(PUBLIC_V1),
        VERSION_CONFIGURACION,
        ALGORITMO,
        "optimization-benchmark/public-v1",
    ):
        assert valor in texto, valor


def test_el_modelo_documenta_su_formulacion_formal():
    """Conjuntos, parametros, variables, jerarquia de objetivos, restricciones, el teorema de la
    reduccion con su prueba, la fijacion por dualidad y la regla de publicacion: no solo prosa."""
    texto = (DOCS / "modelo_optimizacion.md").read_text(encoding="utf-8")
    for seccion in (
        "## Conjuntos",
        "## Parámetros",
        "## Variables",
        "## Restricciones",
        "## La jerarquía de objetivos",
        "## El teorema de la reducción de candidatos",
        "## La fijación por dualidad entre etapas",
        "## La regla de publicación",
    ):
        assert seccion in texto, seccion
    assert "*Prueba.*" in texto and "**Teorema (reducción exacta).**" in texto
    assert "no un IIS" in texto


def test_la_configuracion_documenta_cada_llave_tipo_dimension_y_alcance():
    from motor_cartera.optimizacion import configuracion as c
    from motor_cartera.optimizacion.politica import (
        PRIORIDADES,
        PRODUCTOS,
        TRAMOS,
        Dimension,
        TipoAlcance,
        TipoRestriccion,
    )

    texto = (DOCS / "configuracion_optimizacion.md").read_text(encoding="utf-8")
    for llave in (
        *c.LLAVES_DEL_DOCUMENTO,
        *c.LLAVES_DE_UNA_ADMISIBILIDAD,
        *c.LLAVES_DE_UN_POOL,
        *c.LLAVES_DE_UNA_RESTRICCION,
    ):
        assert f"`{llave}`" in texto, llave
    for valor in (*TipoRestriccion, *Dimension, *TipoAlcance, *PRODUCTOS, *TRAMOS, *PRIORIDADES):
        assert f"`{valor}`" in texto, valor
    assert "Estos límites existen únicamente para medir el optimizador." in texto


def test_la_documentacion_de_optimizacion_no_promete_causalidad():
    """Golden 131: ningun documento ni ninguna respuesta introducen un uplift, una recuperacion
    incremental o un efecto esperado del canal; ninguno promete dinero adicional; el principal dice
    sus limitaciones con todas sus letras."""
    for archivo in DOCUMENTOS_DE_OPTIMIZACION:
        texto = " ".join((DOCS / archivo).read_text(encoding="utf-8").lower().split())
        for campo in ("incremental_recovery", "expected_channel_effect", "uplift_"):
            assert campo not in texto, (archivo, campo)
        for frase in (
            "generará",
            "causó el pago",
            "causado por",
            "impacto causal",
            "tratamiento óptimo",
        ):
            assert frase not in texto, (archivo, frase)
        for oracion in re.split(r"(?<=\.) ", texto):
            if "uplift" in oracion:
                assert "no hay" in oracion, (archivo, oracion)
    principal = " ".join((DOCS / "optimizacion.md").read_text(encoding="utf-8").split())
    for frase in (
        "Es óptimo respecto al modelo declarado. No es una estrategia causalmente óptima.",
        "recuperación positiva esperada bajo la política histórica",
        "no la evidencia de que ese canal causará mayor recuperación",
        "las unidades de recurso no son costos reales",
        "Un óptimo matemático de supuestos incorrectos sigue siendo una mala decisión de negocio.",
    ):
        assert frase in principal, frase
    from motor_cartera.api import esquemas_optimizacion

    fuente = Path(esquemas_optimizacion.__file__).read_text(encoding="utf-8").lower()
    for campo in ("uplift", "incremental_recovery", "expected_channel_effect"):
        assert campo not in fuente, campo


# --- la app (v0.17) -------------------------------------------------------------------------------

DOCUMENTOS_DE_LA_APP = (
    "app.md",
    "contexto_operativo.md",
    "dashboards.md",
    "frontend.md",
    "privacidad_app.md",
)
WEB = DOCS.parent / "web"


def _junto(archivo: Path) -> str:
    return " ".join(archivo.read_text(encoding="utf-8").split())


def test_cada_ruta_de_la_app_esta_documentada():
    import json

    contrato = json.loads((WEB / "src" / "api" / "openapi.json").read_text(encoding="utf-8"))
    texto = (DOCS / "app.md").read_text(encoding="utf-8")
    rutas = [ruta for ruta in contrato["paths"] if ruta.startswith("/app/v1/")]
    assert len(rutas) >= 13
    for ruta in rutas:
        assert f"`GET {ruta}`" in texto, ruta


def test_los_avisos_de_la_app_estan_documentados_tal_cual():
    """Los avisos que la app muestra a la vista (web/src/components/Avisos.tsx) dicen lo mismo en
    la documentacion: la pantalla no promete otra cosa que lo documentado."""
    fuente = " ".join(
        (WEB / "src" / "components" / "Avisos.tsx").read_text(encoding="utf-8").split()
    )
    avisos = dict(re.findall(r'export const (AVISO_\w+) = "([^"]+)";', fuente))
    nombres = {"AVISO_PREDICCION", "AVISO_OPTIMO", "AVISO_ATRIBUCION", "AVISO_DRIFT", "AVISO_DEMO"}
    assert nombres <= set(avisos)
    documentos = " ".join(_junto(DOCS / archivo) for archivo in DOCUMENTOS_DE_LA_APP)
    for nombre in ("AVISO_PREDICCION", "AVISO_OPTIMO", "AVISO_ATRIBUCION", "AVISO_DRIFT"):
        assert avisos[nombre] in documentos, nombre
    assert avisos["AVISO_DEMO"].rstrip(".") in _junto(DOCS / "app.md")
    assert avisos["AVISO_DEMO"].rstrip(".") in _junto(DOCS / "privacidad_app.md")


def test_la_documentacion_de_la_app_no_promete_causalidad():
    """Ninguna promesa causal, salvo para negarla: que una accion causara algo, que un canal
    incrementara la recuperacion, un ROI esperado o un uplift."""
    causales = re.compile(
        r"causar[aá]|incrementar[aá] la recuperaci[oó]n|\broi esperado|\buplift\b", re.IGNORECASE
    )
    for archivo in DOCUMENTOS_DE_LA_APP:
        texto = _junto(DOCS / archivo)
        for hallazgo in causales.finditer(texto):
            antes = texto[max(0, hallazgo.start() - 80) : hallazgo.start()]
            assert re.search(r"\b(no|ni|sin|nunca)\b[^.]*$", antes, re.IGNORECASE), (
                archivo,
                texto[max(0, hallazgo.start() - 80) : hallazgo.end()],
            )
    assert "no implica causalidad" in _junto(DOCS / "dashboards.md")


def test_la_lista_blanca_documentada_es_la_del_proxy():
    """Lo que privacidad_app.md dice que el proxy deja pasar es lo que deja pasar."""
    lista = (WEB / "nginx" / "rutas-api.conf").read_text(encoding="utf-8")
    texto = _junto(DOCS / "privacidad_app.md")
    for prefijo in ("app/v1", "analytics", "predictivo", "optimizacion", "campo-v2", "ruteo-v2"):
        assert f"/api/{prefijo}" in lista, prefijo
    assert "solo GET" in texto and "RUTA_NO_EXPUESTA" in texto and "METODO_NO_PERMITIDO" in texto


# --- produccion, seguridad y operacion (v0.18) ----------------------------------------------------

RUNBOOKS = (
    "service-down",
    "database-down",
    "worker-stuck",
    "queue-backlog",
    "osrm-down",
    "identity-down",
    "backup-restore",
    "secret-rotation",
    "disaster-recovery",
)


def test_cada_alerta_tiene_su_umbral_inicial_y_un_runbook_que_existe():
    reglas = (DOCS.parent / "infra" / "observabilidad" / "alertas.yml").read_text(encoding="utf-8")
    alertas = re.findall(r"- alert: (\w+)", reglas)
    runbooks = re.findall(r"runbook: (docs/runbooks/[\w-]+\.md)", reglas)

    assert len(alertas) >= 15
    assert reglas.count("umbral: inicial") == len(alertas) == len(runbooks)
    for runbook in runbooks:
        assert (DOCS.parent / runbook).exists(), runbook
    observabilidad = (DOCS / "observability.md").read_text(encoding="utf-8")
    for alerta in alertas:
        assert f"`{alerta}`" in observabilidad, alerta


def test_cada_runbook_existe_y_esta_en_el_indice():
    indice = (DOCS / "runbooks" / "README.md").read_text(encoding="utf-8")
    for nombre in RUNBOOKS:
        assert (DOCS / "runbooks" / f"{nombre}.md").exists(), nombre
        assert f"({nombre}.md)" in indice, nombre


def test_la_seguridad_documenta_cada_codigo_de_autenticacion_y_de_autorizacion():
    fuentes = "".join(
        (DOCS.parent / "src" / "motor_cartera" / ruta).read_text(encoding="utf-8")
        for ruta in ("api/autorizacion.py", "seguridad/tokens.py")
    )
    codigos = set(
        re.findall(
            r'"((?:TOKEN|API_KEY|CREDENCIAL|CREDENCIALES|GATEWAY|IDENTIDAD|PERMISO|RUTA)_[A-Z_]+)"',
            fuentes,
        )
    )
    seguridad = (DOCS / "security.md").read_text(encoding="utf-8")

    assert len(codigos) >= 20
    for codigo in sorted(codigos):
        assert f"`{codigo}`" in seguridad, codigo


def test_los_documentos_de_produccion_existen_y_se_enlazan():
    for archivo in (
        "production.md",
        "security.md",
        "threat-model.md",
        "observability.md",
        "slos.md",
        "backups.md",
        "carga.md",
        "cadena-de-suministro.md",
        "cloud-gcp.md",
        "matriz-autorizacion.md",
        "security-review-v018.md",
    ):
        assert (DOCS / archivo).exists(), archivo
    readme = (DOCS.parent / "README.md").read_text(encoding="utf-8")
    for archivo in ("production.md", "security.md", "backups.md", "cloud-gcp.md"):
        assert f"docs/{archivo}" in readme, archivo


def test_la_referencia_de_la_nube_se_declara_nunca_aplicada():
    texto = " ".join((DOCS / "cloud-gcp.md").read_text(encoding="utf-8").split())
    assert "nunca se aplicó y este repositorio no la aplica" in texto
    assert "Lo que falta para un despliegue real" in texto


# --- tipos escritos para el verificador de tipos --------------------------------------------------


def test_los_catalogos_escritos_como_tipos_son_los_de_su_fuente():
    """La API escribe como clases y literales los catalogos que el verificador de tipos no puede
    leer de una tupla; siguen siendo los de su unica fuente, en su orden."""
    from typing import get_args

    from motor_cartera.api.esquemas import Canal, Producto
    from motor_cartera.api.esquemas_app_v1 import TramoAtraso
    from motor_cartera.api.esquemas_campo import DiaSemana
    from motor_cartera.atraso import TRAMOS_ATRASO
    from motor_cartera.campo_v2.configuracion import DIAS
    from motor_cartera.contratos.cartera import CANALES, PRODUCTOS

    assert tuple(p.value for p in Producto) == PRODUCTOS
    assert tuple(c.value for c in Canal) == CANALES
    assert get_args(TramoAtraso) == tuple(etiqueta for etiqueta, _, _ in TRAMOS_ATRASO)
    assert tuple(d.value for d in DiaSemana) == DIAS
    for enumeracion in (Producto, Canal, DiaSemana):
        assert {m.name for m in enumeracion} == {m.value for m in enumeracion}


# --- los enlaces de la documentación --------------------------------------------------------------

BLOQUE_DE_CODIGO = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
CODIGO_EN_LINEA = re.compile(r"`[^`\n]*`")
ENLACE = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
DEFINICION = re.compile(r"^\[[^\]]+\]:\s+<?(\S+?)>?(?:\s+\"[^\"]*\")?\s*$", re.MULTILINE)
ENCABEZADO = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def _ancla(titulo: str) -> str:
    """El identificador que GitHub le da a un encabezado: su texto visible en minúsculas, sin lo
    que no es letra, número, guion bajo, guion ni espacio, y con guiones por espacios."""
    texto = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", titulo).lower()
    return re.sub(r"[^\w\- ]", "", texto).replace(" ", "-")


def _anclas(archivo: Path) -> set[str]:
    """Las anclas de un documento; un encabezado repetido lleva -1, -2... como en GitHub."""
    vistas: dict[str, int] = {}
    anclas = set()
    for titulo in ENCABEZADO.findall(BLOQUE_DE_CODIGO.sub("", archivo.read_text(encoding="utf-8"))):
        base = _ancla(titulo)
        repetidas = vistas.get(base, 0)
        anclas.add(base if repetidas == 0 else f"{base}-{repetidas}")
        vistas[base] = repetidas + 1
    return anclas


def _documentos() -> list[Path]:
    raiz = DOCS.parent
    return [
        raiz / "README.md",
        raiz / "CHANGELOG.md",
        raiz / ".github" / "SECURITY.md",
        *sorted(DOCS.rglob("*.md")),
    ]


def test_los_enlaces_relativos_de_la_documentacion_resuelven():
    """Cada enlace relativo del README, del CHANGELOG, de la política de seguridad y de docs/
    apunta a un archivo o un directorio que existe, y su ancla, si la lleva, a un encabezado de
    ese documento. Sin red: los enlaces externos no se visitan."""
    rotos = []
    for archivo in _documentos():
        texto = archivo.read_text(encoding="utf-8")
        texto = CODIGO_EN_LINEA.sub("", BLOQUE_DE_CODIGO.sub("", texto))
        for destino in ENLACE.findall(texto) + DEFINICION.findall(texto):
            if re.match(r"^[a-z][a-z0-9+.-]*:", destino):
                continue
            ruta, _, ancla = destino.partition("#")
            objetivo = (archivo.parent / ruta).resolve() if ruta else archivo
            nombre = archivo.relative_to(DOCS.parent).as_posix()
            if not objetivo.exists():
                rotos.append(f"{nombre}: {destino} no existe")
            elif ancla and objetivo.suffix == ".md" and ancla not in _anclas(objetivo):
                rotos.append(f"{nombre}: {destino} no es un encabezado de su documento")
    assert not rotos, "\n".join(rotos)


def test_las_anclas_siguen_la_regla_de_github():
    assert _ancla("2. *Fail-closed* en dos niveles: registro y archivo") == (
        "2-fail-closed-en-dos-niveles-registro-y-archivo"
    )
    assert _ancla("217. Recuperación positiva esperada; el riesgo es 1 − p") == (
        "217-recuperación-positiva-esperada-el-riesgo-es-1--p"
    )
    assert _ancla("La importación: `motor-cartera cargar-lifecycle`") == (
        "la-importación-motor-cartera-cargar-lifecycle"
    )
    assert _ancla("Lo que [el índice](README.md) dice") == "lo-que-el-índice-dice"

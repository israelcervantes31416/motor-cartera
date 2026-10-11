"""La configuracion de campo: la validacion fail-closed con todos sus problemas juntos, la forma
canonica y su hash, la jornada de un gestor en una fecha (el golden de los calendarios), el JSONL,
y en PostgreSQL la publicacion, la reutilizacion del mismo contenido, lo que depende de la version
de bases y la inmutabilidad de lo publicado."""

from __future__ import annotations

import copy
import json
import threading
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import func, text
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from motor_cartera.campo_v2.configuracion import (
    ConfiguracionInvalida,
    GestorConfigurado,
    TipoExcepcion,
    Ventana,
    capacidad_efectiva,
    configuracion_de,
    hhmm,
    jornada,
    leer,
    leer_jsonl,
    publicar,
)
from motor_cartera.campo_v2.politica import EstadoJornada
from motor_cartera.db.modelos import (
    CalendarioCampo,
    GestorCampo,
    VentanaJornadaCampo,
    VersionConfiguracionCampo,
)
from motor_cartera.db.sesion import sesion

BASES = str(uuid4())
JUEVES, VIERNES, SABADO, DOMINGO = (date(2026, 10, d) for d in (1, 2, 3, 4))
FESTIVO, MEDIA = date(2026, 11, 16), date(2026, 12, 24)


def _documento(**cambios) -> dict:
    documento = {
        "nombre": "Campo de prueba",
        "bases_version_id": BASES,
        "zona_horaria": "America/Mexico_City",
        "calendarios": [
            {
                "codigo": "LV_PARTIDO",
                "semana": {
                    dia: [{"inicio": "09:00", "fin": "14:00"}, {"inicio": "16:00", "fin": "19:00"}]
                    for dia in ("LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES")
                },
                "excepciones": [
                    {"fecha": FESTIVO.isoformat(), "tipo": "CERRADO", "motivo": "Festivo"},
                    {
                        "fecha": MEDIA.isoformat(),
                        "tipo": "VENTANAS_ESPECIALES",
                        "motivo": "Media jornada",
                        "ventanas": [{"inicio": "09:00", "fin": "13:00"}],
                    },
                ],
            },
            {
                "codigo": "FIN_DE_SEMANA",
                "semana": {"SABADO": [{"inicio": "10:00", "fin": "24:00"}]},
            },
        ],
        "gestores": [
            {
                "codigo": "GESTOR_001",
                "base": "BASE_A",
                "calendario": "LV_PARTIDO",
                "capacidad_cuentas_dia": 20,
                "activo": True,
            },
            {
                "codigo": "GESTOR_002",
                "base": "BASE_A",
                "calendario": "FIN_DE_SEMANA",
                "capacidad_cuentas_dia": 12,
                "activo": True,
            },
            {
                "codigo": "GESTOR_003",
                "base": "BASE_B",
                "calendario": "LV_PARTIDO",
                "capacidad_cuentas_dia": 15,
                "activo": False,
            },
        ],
    }
    documento.update(cambios)
    return documento


def _problemas(documento) -> list[str]:
    with pytest.raises(ConfiguracionInvalida) as error:
        leer(documento)
    return error.value.problemas


# --- leer -----------------------------------------------------------------------------------------


def test_una_configuracion_valida_se_lee_ordenada_y_completa():
    c = leer(_documento())

    assert [cal.codigo for cal in c.calendarios] == ["FIN_DE_SEMANA", "LV_PARTIDO"]
    assert [g.codigo for g in c.gestores] == ["GESTOR_001", "GESTOR_002", "GESTOR_003"]
    lv = c.calendario("LV_PARTIDO")
    assert lv.semana[0] == (Ventana(540, 840), Ventana(960, 1140))
    assert lv.semana[5] == lv.semana[6] == ()
    assert [(e.fecha, e.tipo) for e in lv.excepciones] == [
        (FESTIVO, TipoExcepcion.CERRADO),
        (MEDIA, TipoExcepcion.VENTANAS_ESPECIALES),
    ]
    assert c.calendario("FIN_DE_SEMANA").semana[5] == (Ventana(600, 1440),)
    assert (c.activos, c.excepciones, c.ventanas) == (2, 2, 12)
    assert hhmm(1440) == "24:00"


def test_el_hash_no_depende_del_orden_ni_del_nombre_pero_si_de_cada_dato():
    base = leer(_documento())
    huella = base.hash("a" * 64)
    desordenado = _documento(nombre="Otro nombre")
    desordenado["gestores"] = desordenado["gestores"][::-1]
    desordenado["calendarios"] = desordenado["calendarios"][::-1]
    lv = desordenado["calendarios"][1]
    lv["excepciones"] = lv["excepciones"][::-1]
    lv["semana"] = {dia: ventanas[::-1] for dia, ventanas in reversed(lv["semana"].items())}

    assert leer(desordenado).hash("a" * 64) == huella
    assert base.hash("b" * 64) != huella
    for cambio in (
        lambda d: d["gestores"][0].update(capacidad_cuentas_dia=21),
        lambda d: d["gestores"][0].update(activo=False),
        lambda d: d["gestores"][0].update(base="BASE_B"),
        lambda d: d["gestores"][0].update(calendario="FIN_DE_SEMANA"),
        lambda d: d["calendarios"][0]["semana"]["LUNES"][0].update(fin="13:30"),
        lambda d: d["calendarios"][0]["excepciones"][0].update(motivo="Capacitacion"),
        lambda d: d["calendarios"][0]["excepciones"].pop(),
        lambda d: d.update(zona_horaria="America/Monterrey"),
    ):
        otro = _documento()
        cambio(otro)
        assert leer(otro).hash("a" * 64) != huella


def test_los_textos_se_normalizan_a_nfc():
    compuesto = _documento(nombre="Campo de prueba")
    compuesto["calendarios"][0]["excepciones"][0]["motivo"] = "Capacitación"
    precompuesto = _documento()
    precompuesto["calendarios"][0]["excepciones"][0]["motivo"] = "Capacitación"

    assert leer(compuesto).hash("a" * 64) == leer(precompuesto).hash("a" * 64)


@pytest.mark.parametrize(
    ("cambio", "problema"),
    [
        (lambda d: d["gestores"].append(dict(d["gestores"][0])), "gestores con el codigo repetido"),
        (
            lambda d: d["calendarios"].append(dict(d["calendarios"][0])),
            "calendarios con el codigo repetido",
        ),
        (
            lambda d: d["gestores"][0].update(calendario="NO_EXISTE"),
            "el calendario NO_EXISTE no existe",
        ),
        (
            lambda d: d["calendarios"][0]["semana"]["LUNES"].append(
                {"inicio": "13:00", "fin": "15:00"}
            ),
            "se enciman",
        ),
        (
            lambda d: d["calendarios"][0]["semana"]["LUNES"].append(
                {"inicio": "10:00", "fin": "11:00"}
            ),
            "se enciman",
        ),
        (lambda d: d["calendarios"][0]["semana"]["LUNES"][0].update(fin="09:00"), "es antes que"),
        (lambda d: d["calendarios"][0]["semana"]["LUNES"][0].update(fin="25:00"), "HH:MM"),
        (lambda d: d["calendarios"][0]["semana"]["LUNES"][0].update(inicio="9:00"), "HH:MM"),
        (lambda d: d["calendarios"][0]["semana"]["LUNES"][0].update(inicio="24:00"), "24:00"),
        (lambda d: d["gestores"][0].update(capacidad_cuentas_dia=-5), "entre 1 y 1,000"),
        (lambda d: d["gestores"][0].update(capacidad_cuentas_dia=0), "entre 1 y 1,000"),
        (lambda d: d["gestores"][0].update(capacidad_cuentas_dia=1001), "entre 1 y 1,000"),
        (lambda d: d["gestores"][0].update(capacidad_cuentas_dia="20"), "es un entero"),
        (lambda d: d["gestores"][0].update(capacidad_cuentas_dia=20.5), "es un entero"),
        (lambda d: d["gestores"][0].update(capacidad_cuentas_dia=True), "es un entero"),
        (lambda d: d["gestores"][0].update(activo="si"), "activo es true o false"),
        (lambda d: d["gestores"][0].update(nombre="Juan Perez"), "no se conocen: nombre"),
        (lambda d: d["gestores"][0].update(telefono="2221234567"), "no se conocen: telefono"),
        (lambda d: d["gestores"][0].update(codigo="juan@correo.mx"), "codigo cumple"),
        (lambda d: d["gestores"][0].pop("activo"), "no trae activo"),
        (lambda d: d.update(capacidad=10), "no se conocen: capacidad"),
        (lambda d: d["calendarios"][0].update(turno="A"), "no se conocen: turno"),
        (lambda d: d["calendarios"][0]["semana"]["LUNES"][0].update(pausa=True), "no se conocen"),
        (lambda d: d["calendarios"][0]["semana"].update(FERIADO=[]), "dias que no se conocen"),
        (lambda d: d.update(zona_horaria="Mexico/Puebla_Centro"), "zona_horaria"),
        (lambda d: d.update(zona_horaria=""), "zona_horaria"),
        (lambda d: d.update(bases_version_id="no-es-uuid"), "bases_version_id"),
        (lambda d: d.update(gestores=[]), "gestores es una lista no vacia"),
        (lambda d: d.update(calendarios=[]), "calendarios es una lista no vacia"),
        (
            lambda d: d["calendarios"][0]["excepciones"].append(
                {"fecha": FESTIVO.isoformat(), "tipo": "CERRADO"}
            ),
            "fechas con mas de una excepcion",
        ),
        (
            lambda d: d["calendarios"][0]["excepciones"][0].update(
                ventanas=[{"inicio": "09:00", "fin": "10:00"}]
            ),
            "una excepcion CERRADO no tiene ventanas",
        ),
        (
            lambda d: d["calendarios"][0]["excepciones"][1].pop("ventanas"),
            "tiene al menos una ventana",
        ),
        (
            lambda d: d["calendarios"][0]["excepciones"][1].update(tipo="MEDIO_DIA"),
            "CERRADO o VENTANAS_ESPECIALES",
        ),
        (
            lambda d: d["calendarios"][0]["excepciones"][0].update(fecha="2026-02-30"),
            "una fecha AAAA-MM-DD que existe",
        ),
    ],
)
def test_lo_que_no_se_puede_publicar_se_rechaza_entero(cambio, problema):
    documento = _documento()
    cambio(documento)

    problemas = _problemas(documento)

    assert any(problema in p for p in problemas), problemas


def test_todos_los_problemas_se_reportan_juntos():
    documento = _documento()
    documento["gestores"][0]["capacidad_cuentas_dia"] = -1
    documento["gestores"][1]["calendario"] = "NO_EXISTE"
    documento["gestores"].append(dict(documento["gestores"][2]))
    documento["calendarios"][0]["semana"]["MARTES"].append({"inicio": "13:00", "fin": "15:00"})

    problemas = _problemas(documento)

    assert len(problemas) == 4
    assert any("GESTOR_001" in p and "entre 1 y" in p for p in problemas)
    assert any("NO_EXISTE" in p for p in problemas)
    assert any("GESTOR_003" in p and "repetido" in p for p in problemas)
    assert any("MARTES" in p and "se enciman" in p for p in problemas)


def test_un_documento_que_no_es_un_objeto_se_rechaza():
    for documento in ([], "texto", None, 3):
        assert _problemas(documento)


# --- la jornada: el golden de los calendarios -----------------------------------------------------


def _gestor(codigo: str) -> tuple[GestorConfigurado, object]:
    c = leer(_documento())
    g = next(g for g in c.gestores if g.codigo == codigo)
    return g, c.calendario(g.calendario)


def test_un_gestor_disponible_en_un_dia_con_dos_ventanas():
    g, calendario = _gestor("GESTOR_001")

    estado, ventanas = jornada(g, calendario, JUEVES)

    assert estado == EstadoJornada.JORNADA_SEMANAL
    assert [v.canonica() for v in ventanas] == [["09:00", "14:00"], ["16:00", "19:00"]]
    assert capacidad_efectiva(g, calendario, JUEVES) == 20


def test_un_gestor_inactivo_no_tiene_capacidad():
    g, calendario = _gestor("GESTOR_003")

    assert jornada(g, calendario, JUEVES) == (EstadoJornada.INACTIVO, ())
    assert capacidad_efectiva(g, calendario, JUEVES) == 0


def test_un_dia_sin_ventanas_no_tiene_jornada():
    g, calendario = _gestor("GESTOR_001")

    assert jornada(g, calendario, SABADO) == (EstadoJornada.SIN_JORNADA, ())
    assert capacidad_efectiva(g, calendario, DOMINGO) == 0
    otro, fin_de_semana = _gestor("GESTOR_002")
    assert jornada(otro, fin_de_semana, JUEVES)[0] == EstadoJornada.SIN_JORNADA
    assert jornada(otro, fin_de_semana, SABADO) == (
        EstadoJornada.JORNADA_SEMANAL,
        (Ventana(600, 1440),),
    )


def test_una_excepcion_cerrado_cierra_el_dia_aunque_la_semana_diga_otra_cosa():
    g, calendario = _gestor("GESTOR_001")

    assert FESTIVO.isoweekday() == 1  # un lunes
    assert jornada(g, calendario, FESTIVO) == (EstadoJornada.CERRADO_POR_EXCEPCION, ())
    assert capacidad_efectiva(g, calendario, FESTIVO) == 0


def test_una_excepcion_con_ventanas_especiales_las_usa_sin_reducir_el_cupo():
    g, calendario = _gestor("GESTOR_001")

    estado, ventanas = jornada(g, calendario, MEDIA)

    assert estado == EstadoJornada.VENTANAS_ESPECIALES
    assert [v.canonica() for v in ventanas] == [["09:00", "13:00"]]
    # Una media jornada no reduce el cupo: el cupo se declara, no se deriva de las horas.
    assert capacidad_efectiva(g, calendario, MEDIA) == 20


# --- JSONL ----------------------------------------------------------------------------------------


def _jsonl(documento: dict) -> str:
    cabecera = {k: documento[k] for k in ("nombre", "bases_version_id", "zona_horaria")}
    lineas = [{"registro": "configuracion", **cabecera}]
    lineas += [{"registro": "calendario", **c} for c in documento["calendarios"]]
    lineas += [{"registro": "gestor", **g} for g in documento["gestores"]]
    return "\n".join(json.dumps(linea) for linea in lineas) + "\n\n"


def test_un_jsonl_es_la_misma_configuracion():
    assert leer_jsonl(_jsonl(_documento())).hash("a" * 64) == leer(_documento()).hash("a" * 64)


@pytest.mark.parametrize(
    ("texto", "problema"),
    [
        ('{"registro": "gestor", "codigo": "X"}\n', "falta la linea con el registro configuracion"),
        ("{no es json\n", "no es JSON"),
        ('{"registro": "base"}\n', "registro es uno de"),
        ('["registro"]\n', "registro es uno de"),
    ],
)
def test_un_jsonl_mal_formado_se_rechaza(texto, problema):
    with pytest.raises(ConfiguracionInvalida) as error:
        leer_jsonl(texto)
    assert any(problema in p for p in error.value.problemas)


def test_un_jsonl_con_dos_cabeceras_se_rechaza():
    texto = _jsonl(_documento())
    primera = texto.splitlines()[0]
    with pytest.raises(ConfiguracionInvalida) as error:
        leer_jsonl(primera + "\n" + texto)
    assert any("repite el registro configuracion" in p for p in error.value.problemas)


# --- publicar, en PostgreSQL ----------------------------------------------------------------------


@pytest.fixture
def bases(bd):
    from territorial_v2_escenarios import base, publicar_bases

    return publicar_bases(
        base("BASE_A", 19.05, -98.25, ["21114"]),
        base("BASE_B", 19.05, -98.05, ["21015"]),
        base("BASE_C", 19.05, -98.00, ["21015"], activa=False),
    )


def _de(bases) -> dict:
    return _documento(bases_version_id=str(bases.bases_version_id))


def _publicar(documento: dict):
    with sesion() as s:
        publicada = publicar(s, leer(documento))
        s.commit()
        s.refresh(publicada.version)
        return publicada


def _cuantos(modelo) -> int:
    with sesion() as s:
        return s.exec(select(func.count()).select_from(modelo)).one()


def test_publicar_escribe_la_version_con_todo_lo_suyo(bases):
    publicada = _publicar(_de(bases))

    v = publicada.version
    assert publicada.creada
    assert (v.estado, v.version_configuracion, v.zona_horaria) == (
        "PUBLICADA",
        "campo-configuracion/v1",
        "America/Mexico_City",
    )
    assert (v.calendarios, v.ventanas, v.excepciones, v.gestores, v.gestores_activos) == (
        2,
        12,
        2,
        3,
        2,
    )
    assert v.capacidad_declarada == 32
    assert v.metadatos["gestores_por_base"] == {
        "BASE_A": {"gestores": 2, "activos": 2, "capacidad_declarada": 32},
        "BASE_B": {"gestores": 1, "activos": 0, "capacidad_declarada": 0},
    }
    assert v.creada_en == v.publicada_en
    assert (_cuantos(CalendarioCampo), _cuantos(VentanaJornadaCampo), _cuantos(GestorCampo)) == (
        2,
        12,
        3,
    )
    with sesion() as s:
        leida = configuracion_de(s, s.get_one(VersionConfiguracionCampo, v.id))
    assert leida.hash(bases.hash_configuracion) == v.hash_configuracion
    assert leida == leer(_de(bases))


def test_el_mismo_contenido_es_la_misma_version(bases):
    primera = _publicar(_de(bases))
    otra = _de(bases)
    otra["nombre"] = "Otra etiqueta"
    otra["gestores"] = otra["gestores"][::-1]

    segunda = _publicar(otra)

    assert not segunda.creada
    assert segunda.version.configuracion_campo_version_id == (
        primera.version.configuracion_campo_version_id
    )
    assert _cuantos(VersionConfiguracionCampo) == 1
    cambiada = _de(bases)
    cambiada["gestores"][0]["capacidad_cuentas_dia"] = 10
    assert _publicar(cambiada).creada
    assert _cuantos(VersionConfiguracionCampo) == 2


@pytest.mark.parametrize(
    ("cambio", "problema"),
    [
        (lambda d: d["gestores"][0].update(base="BASE_Z"), "la base BASE_Z no existe"),
        (lambda d: d["gestores"][0].update(base="BASE_C"), "esta inactiva"),
        (lambda d: d.update(bases_version_id=str(uuid4())), "no existe la version de bases"),
    ],
)
def test_lo_que_depende_de_la_version_de_bases_se_revisa_antes_de_escribir(bases, cambio, problema):
    documento = _de(bases)
    cambio(documento)

    with sesion() as s, pytest.raises(ConfiguracionInvalida) as error:
        publicar(s, leer(documento))

    assert any(problema in p for p in error.value.problemas)
    assert _cuantos(VersionConfiguracionCampo) == 0
    assert _cuantos(GestorCampo) == 0


def test_dos_publicaciones_simultaneas_del_mismo_contenido_dan_una_version(bases):
    barrera = threading.Barrier(2)
    salidas: list = []

    def publicar_a_la_vez():
        with sesion() as s:
            barrera.wait(10)
            salidas.append(publicar(s, leer(_de(bases))))
            s.commit()

    hilos = [threading.Thread(target=publicar_a_la_vez) for _ in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(30)

    assert len(salidas) == 2
    assert _cuantos(VersionConfiguracionCampo) == 1
    assert _cuantos(GestorCampo) == 3


@pytest.mark.parametrize(
    "tabla",
    [
        "version_configuracion_campo",
        "calendario_campo",
        "excepcion_calendario_campo",
        "ventana_jornada_campo",
        "gestor_campo",
    ],
)
def test_una_configuracion_publicada_no_se_reescribe(bases, tabla):
    _publicar(_de(bases))
    with sesion() as s:
        (columna,) = s.execute(
            text(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = "
                f"current_schema() AND table_name = '{tabla}' AND column_name <> 'id' "
                "ORDER BY ordinal_position LIMIT 1"
            )
        ).one()

    for sentencia in (f"UPDATE {tabla} SET {columna} = {columna}", f"DELETE FROM {tabla}"):
        with pytest.raises(DBAPIError) as error, sesion() as s:
            s.execute(text(sentencia))
        assert error.value.orig.diag.constraint_name == "tr_configuracion_campo_inmutable"
        assert "no se reescribe" in str(error.value)


def test_la_base_rechaza_dos_ventanas_que_se_enciman_aunque_no_pasen_por_leer(bases):
    publicada = _publicar(_de(bases))
    with sesion() as s:
        calendario = s.exec(
            select(CalendarioCampo.id).where(
                CalendarioCampo.version_configuracion_campo_id == publicada.version.id,
                CalendarioCampo.codigo == "FIN_DE_SEMANA",
            )
        ).one()
    with pytest.raises(DBAPIError) as error, sesion() as s:
        s.execute(
            text(
                "INSERT INTO ventana_jornada_campo (calendario_campo_id, dia_semana, "
                "minuto_inicio, minuto_fin) VALUES (:c, 6, 700, 800)"
            ),
            {"c": calendario},
        )
        s.commit()
    assert error.value.orig.diag.constraint_name == "tr_ventana_campo_sin_encimarse"
    # Tocarse no es encimarse: la de 9 a 10 termina donde empieza la de 10 a 24.
    with sesion() as s:
        s.execute(
            text(
                "INSERT INTO ventana_jornada_campo (calendario_campo_id, dia_semana, "
                "minuto_inicio, minuto_fin) VALUES (:c, 6, 540, 600)"
            ),
            {"c": calendario},
        )
        s.rollback()


def test_la_configuracion_no_guarda_datos_de_personas(bases):
    """Ni el documento ni las tablas tienen donde poner un nombre, un correo o un telefono: un
    gestor es su codigo."""
    columnas = set()
    with sesion() as s:
        for tabla in ("gestor_campo", "calendario_campo", "version_configuracion_campo"):
            columnas |= set(
                s.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns WHERE table_schema = "
                        f"current_schema() AND table_name = '{tabla}'"
                    )
                ).scalars()
            )
    for dato in ("telefono", "correo", "email", "curp", "rfc", "nombre_gestor", "empleado"):
        assert dato not in columnas
    documento = copy.deepcopy(_de(bases))
    documento["gestores"][0]["correo"] = "x@y.z"
    assert any("no se conocen: correo" in p for p in _problemas(documento))

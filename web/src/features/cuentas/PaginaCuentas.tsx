/**
 * El explorador de cuentas: las cuentas del corte del contexto, filtradas, ordenadas y paginadas
 * en el servidor (GET /app/v1/cuentas). El navegador nunca baja la cartera: pide una pagina de
 * hasta 100. Los filtros, el orden y la pagina viven en la URL. La busqueda por el principio del
 * cuenta_id espera a que se deje de escribir, y una peticion vieja se cancela.
 */
import { createColumnHelper } from "@tanstack/react-table";
import { useEffect, useId, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { useCuentas, type FiltrosDeCuentas } from "../../api/consultas";
import type { CuentaListada } from "../../api/tipos";
import { ConContexto, type ContextoListo } from "../../app/contexto";
import { Aviso, AVISO_OPTIMO, AVISO_PREDICCION } from "../../components/Avisos";
import { Cargando, ErrorDeCarga } from "../../components/Carga";
import { InsigniaEstado, ValorOpcional } from "../../components/Estado";
import { Paginacion, TablaDatos, TAMANOS_DE_PAGINA } from "../../components/Tabla";
import { rutaConContexto, type Contexto } from "../../lib/contexto";
import {
  abreviar,
  formatearCentavos,
  formatearEntero,
  formatearMonto,
  formatearPorcentaje,
} from "../../lib/formato";

export const ESPERA_DE_BUSQUEDA_MS = 350;

const TRAMOS = ["0", "1-30", "31-60", "61-90", "91+"] as const;
const PRIORIDADES = ["MUY_ALTA", "ALTA", "MEDIA", "BAJA"] as const;
const CANALES = ["DIGITAL", "TELEFONICA", "CAMPO"] as const;
const ESTADOS_PREDICCION = ["COMPLETA", "CON_IMPUTACION"] as const;
const ESTADOS_OPTIMIZACION = ["ASIGNADA", "NO_SELECCIONADA", "NO_ELEGIBLE"] as const;
const ORDENES = [
  "CUENTA",
  "SALDO_TOTAL",
  "DIAS_ATRASO",
  "P_RECUPERACION_POSITIVA_30D",
  "RECUPERACION_POSITIVA_ESPERADA_30D",
] as const;
const PREFIJO = /^[0-9a-fA-F-]{4,36}$/;
const PRODUCTO = /^[A-Z0-9_]{1,20}$/;

function de<T extends string>(lista: readonly T[], valor: string | null): T | undefined {
  return lista.find((v) => v === valor);
}

/** Los filtros de la URL, solo los validos: lo demas no se manda. */
export function filtrosDeLaUrl(busqueda: URLSearchParams, contexto: Contexto): FiltrosDeCuentas {
  const pagina = Number(busqueda.get("pagina") ?? "1");
  const porPagina = Number(busqueda.get("por_pagina") ?? "50");
  const cuenta = busqueda.get("cuenta") ?? "";
  const producto = busqueda.get("producto") ?? "";
  const orden = de(ORDENES, busqueda.get("orden")) ?? "CUENTA";
  const necesitaScoring = orden.startsWith("P_") || orden.startsWith("RECUPERACION");
  const filtros: Record<string, string | number> = {
    pagina: Number.isInteger(pagina) && pagina >= 1 ? pagina : 1,
    por_pagina: (TAMANOS_DE_PAGINA as readonly number[]).includes(porPagina) ? porPagina : 50,
    orden: necesitaScoring && contexto.scoring === undefined ? "CUENTA" : orden,
    sentido: busqueda.get("sentido") === "DESC" ? "DESC" : "ASC",
  };
  if (PREFIJO.test(cuenta)) filtros["cuenta"] = cuenta;
  if (PRODUCTO.test(producto)) filtros["producto"] = producto;
  const tramo = de(TRAMOS, busqueda.get("atraso_bucket"));
  if (tramo) filtros["atraso_bucket"] = tramo;
  if (contexto.decision !== undefined) {
    const prioridad = de(PRIORIDADES, busqueda.get("prioridad"));
    if (prioridad) filtros["prioridad"] = prioridad;
    const canal = de(CANALES, busqueda.get("canal"));
    if (canal) filtros["canal"] = canal;
  }
  if (contexto.scoring !== undefined) {
    const estado = de(ESTADOS_PREDICCION, busqueda.get("estado_prediccion"));
    if (estado) filtros["estado_prediccion"] = estado;
  }
  if (contexto.optimizacion !== undefined) {
    const estado = de(ESTADOS_OPTIMIZACION, busqueda.get("estado_optimizacion"));
    if (estado) filtros["estado_optimizacion"] = estado;
  }
  return filtros;
}

export function PaginaCuentas() {
  return <ConContexto titulo="Cuentas">{(listo) => <Explorador {...listo} />}</ConContexto>;
}

const columna = createColumnHelper<CuentaListada>();

function Explorador({ contexto, validado }: ContextoListo) {
  const [busqueda, setBusqueda] = useSearchParams();
  const filtros = filtrosDeLaUrl(busqueda, contexto);
  const cuentas = useCuentas(contexto, filtros, true);
  const [texto, setTexto] = useState(busqueda.get("cuenta") ?? "");
  const idBusqueda = useId();

  function cambiar(cambios: Record<string, string | number | undefined>, reiniciarPagina = true) {
    const nueva = new URLSearchParams(busqueda);
    for (const [llave, valor] of Object.entries(cambios)) {
      if (valor === undefined || valor === "") nueva.delete(llave);
      else nueva.set(llave, String(valor));
    }
    if (reiniciarPagina && !("pagina" in cambios)) nueva.delete("pagina");
    setBusqueda(nueva);
  }

  // La busqueda espera a que se deje de escribir; la consulta anterior la cancela TanStack Query.
  useEffect(() => {
    const actual = busqueda.get("cuenta") ?? "";
    if (texto === actual) return undefined;
    const temporizador = window.setTimeout(() => {
      const nueva = new URLSearchParams(busqueda);
      if (texto === "") nueva.delete("cuenta");
      else nueva.set("cuenta", texto);
      nueva.delete("pagina");
      setBusqueda(nueva, { replace: true });
    }, ESPERA_DE_BUSQUEDA_MS);
    return () => {
      window.clearTimeout(temporizador);
    };
  }, [texto, busqueda, setBusqueda]);

  const conDecision = contexto.decision !== undefined;
  const conScoring = contexto.scoring !== undefined;
  const conOptimizacion = contexto.optimizacion !== undefined;
  const columnas = [
    columna.display({
      id: "cuenta",
      header: "Cuenta",
      meta: { orden: "CUENTA" },
      cell: ({ row }) => (
        <Link
          to={rutaConContexto(`/cuentas/${encodeURIComponent(row.original.cuenta_id)}`, contexto)}
          aria-label={`Abrir la cuenta ${row.original.cuenta_id}`}
        >
          <code>{abreviar(row.original.cuenta_id, 13)}</code>
        </Link>
      ),
    }),
    columna.display({
      id: "producto",
      header: "Producto",
      cell: ({ row }) => row.original.producto,
    }),
    columna.display({
      id: "saldo",
      header: "Saldo total",
      meta: { orden: "SALDO_TOTAL", numerica: true },
      cell: ({ row }) => formatearMonto(row.original.saldo_total),
    }),
    columna.display({
      id: "atraso",
      header: "Días de atraso",
      meta: { orden: "DIAS_ATRASO", numerica: true },
      cell: ({ row }) =>
        `${formatearEntero(row.original.dias_atraso)} (${row.original.atraso_bucket})`,
    }),
    ...(conDecision
      ? [
          columna.display({
            id: "prioridad",
            header: "Prioridad",
            cell: ({ row }) =>
              row.original.prioridad === null ? (
                <InsigniaEstado estado="SIN_RESULTADO" />
              ) : (
                <InsigniaEstado estado={row.original.prioridad} />
              ),
          }),
          columna.display({
            id: "canal",
            header: "Canal recomendado",
            cell: ({ row }) =>
              row.original.canal_recomendado ?? <InsigniaEstado estado="SIN_RESULTADO" />,
          }),
        ]
      : []),
    ...(conScoring
      ? [
          columna.display({
            id: "p",
            header: "p recuperación 30 d",
            meta: { orden: "P_RECUPERACION_POSITIVA_30D", numerica: true },
            cell: ({ row }) => (
              <ValorOpcional
                valor={row.original.p_recuperacion_positiva_30d}
                formatear={formatearPorcentaje}
              />
            ),
          }),
          columna.display({
            id: "esperada",
            header: "Recuperación positiva esperada",
            meta: { orden: "RECUPERACION_POSITIVA_ESPERADA_30D", numerica: true },
            cell: ({ row }) => (
              <ValorOpcional
                valor={row.original.recuperacion_positiva_esperada_30d}
                formatear={formatearMonto}
              />
            ),
          }),
          columna.display({
            id: "estado_prediccion",
            header: "Predicción",
            cell: ({ row }) => (
              <InsigniaEstado estado={row.original.estado_prediccion ?? "SIN_RESULTADO"} />
            ),
          }),
        ]
      : []),
    ...(conOptimizacion
      ? [
          columna.display({
            id: "estado_optimizacion",
            header: "Optimización",
            cell: ({ row }) => (
              <InsigniaEstado estado={row.original.estado_optimizacion ?? "SIN_RESULTADO"} />
            ),
          }),
          columna.display({
            id: "canal_asignado",
            header: "Canal asignado",
            cell: ({ row }) => row.original.canal_asignado ?? "—",
          }),
          columna.display({
            id: "valor",
            header: "Valor de oportunidad",
            meta: { numerica: true },
            cell: ({ row }) => (
              <ValorOpcional
                valor={row.original.valor_oportunidad_centavos}
                formatear={formatearCentavos}
              />
            ),
          }),
        ]
      : []),
  ];

  const ordenActual = {
    columna: filtros.orden ?? "CUENTA",
    descendente: filtros.sentido === "DESC",
  };

  return (
    <>
      <h1>Cuentas</h1>
      <p className="entrada">
        Las {formatearEntero(validado.corte.cuentas)} cuentas del corte, paginadas en el servidor.
        Cada columna es de la ejecución que el contexto declara; la cuenta es su identificador
        técnico, sin datos personales.
      </p>
      {conScoring ? <Aviso tono="aviso">{AVISO_PREDICCION}</Aviso> : null}
      {conOptimizacion ? <Aviso tono="aviso">{AVISO_OPTIMO}</Aviso> : null}
      <form
        className="filtros"
        role="search"
        aria-label="Filtros de cuentas"
        onSubmit={(evento) => {
          evento.preventDefault();
        }}
      >
        <div className="campo">
          <label htmlFor={idBusqueda}>Identificador técnico (principio)</label>
          <input
            id={idBusqueda}
            type="search"
            inputMode="text"
            autoComplete="off"
            spellCheck={false}
            placeholder="p. ej. 3f2a9c"
            value={texto}
            onChange={(evento) => {
              setTexto(evento.target.value.trim());
            }}
            aria-describedby={`${idBusqueda}-ayuda`}
          />
          <span id={`${idBusqueda}-ayuda`} className="campo__ayuda">
            Cuatro o más dígitos hexadecimales del cuenta_id.
          </span>
        </div>
        <Selector
          etiqueta="Producto"
          valor={filtros.producto ?? ""}
          opciones={[]}
          libre
          onCambio={(v) => {
            cambiar({ producto: v?.toUpperCase() });
          }}
        />
        <Selector
          etiqueta="Tramo de atraso"
          valor={filtros.atraso_bucket ?? ""}
          opciones={TRAMOS}
          onCambio={(v) => {
            cambiar({ atraso_bucket: v });
          }}
        />
        {conDecision ? (
          <>
            <Selector
              etiqueta="Prioridad"
              valor={filtros.prioridad ?? ""}
              opciones={PRIORIDADES}
              onCambio={(v) => {
                cambiar({ prioridad: v });
              }}
            />
            <Selector
              etiqueta="Canal recomendado"
              valor={filtros.canal ?? ""}
              opciones={CANALES}
              onCambio={(v) => {
                cambiar({ canal: v });
              }}
            />
          </>
        ) : null}
        {conScoring ? (
          <Selector
            etiqueta="Estado de la predicción"
            valor={filtros.estado_prediccion ?? ""}
            opciones={ESTADOS_PREDICCION}
            onCambio={(v) => {
              cambiar({ estado_prediccion: v });
            }}
          />
        ) : null}
        {conOptimizacion ? (
          <Selector
            etiqueta="Estado en la optimización"
            valor={filtros.estado_optimizacion ?? ""}
            opciones={ESTADOS_OPTIMIZACION}
            onCambio={(v) => {
              cambiar({ estado_optimizacion: v });
            }}
          />
        ) : null}
      </form>
      {cuentas.isPending ? (
        <Cargando que="las cuentas" />
      ) : cuentas.isError ? (
        <ErrorDeCarga
          error={cuentas.error}
          reintentar={() => {
            void cuentas.refetch();
          }}
        />
      ) : (
        <div className={cuentas.isPlaceholderData ? "actualizando" : undefined}>
          <TablaDatos
            titulo="Cuentas del corte"
            columnas={columnas}
            filas={cuentas.data.elementos}
            claveDeFila={(c) => c.cuenta_id}
            orden={ordenActual}
            onOrden={(o) => {
              cambiar({ orden: o.columna, sentido: o.descendente ? "DESC" : "ASC" });
            }}
            vacio="Ninguna cuenta del corte cumple los filtros."
          />
          <Paginacion
            total={cuentas.data.total}
            pagina={cuentas.data.pagina}
            porPagina={cuentas.data.por_pagina}
            etiqueta="las cuentas"
            onPagina={(p) => {
              cambiar({ pagina: p }, false);
            }}
            onPorPagina={(n) => {
              cambiar({ por_pagina: n });
            }}
          />
        </div>
      )}
    </>
  );
}

function Selector({
  etiqueta,
  valor,
  opciones,
  onCambio,
  libre = false,
}: {
  readonly etiqueta: string;
  readonly valor: string;
  readonly opciones: readonly string[];
  readonly onCambio: (valor: string | undefined) => void;
  readonly libre?: boolean;
}) {
  const id = useId();
  return (
    <div className="campo">
      <label htmlFor={id}>{etiqueta}</label>
      {libre ? (
        <input
          id={id}
          type="text"
          autoComplete="off"
          defaultValue={valor}
          placeholder="Todos"
          onBlur={(evento) => {
            onCambio(evento.target.value.trim() || undefined);
          }}
          onKeyDown={(evento) => {
            if (evento.key === "Enter") onCambio(evento.currentTarget.value.trim() || undefined);
          }}
        />
      ) : (
        <select
          id={id}
          value={valor}
          onChange={(evento) => {
            onCambio(evento.target.value || undefined);
          }}
        >
          <option value="">Todos</option>
          {opciones.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}

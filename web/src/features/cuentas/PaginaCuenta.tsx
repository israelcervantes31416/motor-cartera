/**
 * La Cuenta 360 en el contexto: su presencia y su snapshot al corte, y lo que cada ejecucion
 * declarada publico de ella (GET /app/v1/cuentas/{cuenta_id}). Cada pestana es una URL, y las que
 * leen listas o explicaciones las piden solo al abrirse. La cuenta es su identificador tecnico: ni
 * su CLIENTE_UNICO ni un dato personal.
 */
import { Link, useParams } from "react-router";

import { ErrorDeApi } from "../../api/cliente";
import { useCuenta } from "../../api/consultas";
import type { Cuenta360 } from "../../api/tipos";
import { ConContexto, type ContextoListo } from "../../app/contexto";
import { Aviso, AVISO_OPTIMO, AVISO_PREDICCION } from "../../components/Avisos";
import { Cargando, ErrorDeCarga } from "../../components/Carga";
import { Copiable } from "../../components/Copiar";
import { InsigniaEstado, ValorOpcional } from "../../components/Estado";
import { Ficha } from "../../components/Kpi";
import { Pestanas } from "../../components/Pestanas";
import { esUuid, rutaConContexto } from "../../lib/contexto";
import {
  etiquetaDeCodigo,
  formatearEntero,
  formatearFecha,
  formatearMonto,
  formatearPorcentaje,
} from "../../lib/formato";
import {
  PestanaCampo,
  PestanaDecision,
  PestanaEvidencia,
  PestanaGeografia,
  PestanaGestiones,
  PestanaHistoria,
  PestanaOptimizacion,
  PestanaPagos,
  PestanaPredictivo,
  PestanaRuteo,
} from "./PestanasDeCuenta";

export const PESTANAS = [
  ["resumen", "Resumen"],
  ["historia", "Historia"],
  ["pagos", "Pagos"],
  ["gestiones", "Gestiones"],
  ["decision", "Decisión"],
  ["geografia", "Geografía y territorial"],
  ["campo", "Campo"],
  ["ruteo", "Ruteo"],
  ["predictivo", "Predictivo"],
  ["optimizacion", "Optimización"],
  ["evidencia", "Evidencia"],
] as const;

export function PaginaCuenta() {
  const { cuentaId = "", pestana = "resumen" } = useParams();
  return (
    <ConContexto titulo="Cuenta 360">
      {(listo) => <Cuenta id={cuentaId} pestana={pestana} {...listo} />}
    </ConContexto>
  );
}

function Cuenta({
  id,
  pestana,
  contexto,
  validado,
}: ContextoListo & { readonly id: string; readonly pestana: string }) {
  const valido = esUuid(id);
  const cuenta = useCuenta(id, contexto, valido);
  const base = `/cuentas/${encodeURIComponent(id)}`;
  if (!valido) {
    return (
      <section className="tarjeta">
        <h1>Cuenta 360</h1>
        <p>
          <code>{id}</code> no es un identificador de cuenta: tiene que ser un UUID.
        </p>
        <Link className="boton" to={rutaConContexto("/cuentas", contexto)}>
          Volver a las cuentas
        </Link>
      </section>
    );
  }
  const conocida = PESTANAS.some(([clave]) => clave === pestana);
  return (
    <>
      <h1>
        Cuenta 360 <Copiable valor={id} etiqueta="el identificador de la cuenta" largo={13} />
      </h1>
      <Pestanas
        etiqueta="Secciones de la cuenta"
        pestanas={PESTANAS.map(([clave, etiqueta]) => ({
          etiqueta,
          a: rutaConContexto(clave === "resumen" ? base : `${base}/${clave}`, contexto),
        }))}
      />
      {cuenta.isPending ? (
        <Cargando que="la cuenta" />
      ) : cuenta.isError ? (
        cuenta.error instanceof ErrorDeApi && cuenta.error.codigo === "CUENTA_NO_ENCONTRADA" ? (
          <section className="tarjeta alerta alerta--aviso" role="alert">
            <h2>Cuenta no encontrada</h2>
            <p>No existe una cuenta canónica con el identificador {id}.</p>
            <Link className="boton" to={rutaConContexto("/cuentas", contexto)}>
              Volver a las cuentas
            </Link>
          </section>
        ) : (
          <ErrorDeCarga
            error={cuenta.error}
            reintentar={() => {
              void cuenta.refetch();
            }}
          />
        )
      ) : !conocida ? (
        <p className="alerta alerta--aviso">La cuenta no tiene la sección {pestana}.</p>
      ) : (
        <ContenidoDePestana
          pestana={pestana}
          cuenta={cuenta.data}
          contexto={contexto}
          validado={validado}
        />
      )}
    </>
  );
}

function ContenidoDePestana({
  pestana,
  cuenta,
  contexto,
  validado,
}: ContextoListo & { readonly pestana: string; readonly cuenta: Cuenta360 }) {
  switch (pestana) {
    case "historia":
      return <PestanaHistoria cuenta={cuenta} validado={validado} />;
    case "pagos":
      return <PestanaPagos cuenta={cuenta} />;
    case "gestiones":
      return <PestanaGestiones cuenta={cuenta} validado={validado} />;
    case "decision":
      return <PestanaDecision cuenta={cuenta} />;
    case "geografia":
      return <PestanaGeografia cuenta={cuenta} />;
    case "campo":
      return <PestanaCampo cuenta={cuenta} />;
    case "ruteo":
      return <PestanaRuteo cuenta={cuenta} contexto={contexto} />;
    case "predictivo":
      return <PestanaPredictivo cuenta={cuenta} />;
    case "optimizacion":
      return <PestanaOptimizacion cuenta={cuenta} />;
    case "evidencia":
      return <PestanaEvidencia cuenta={cuenta} validado={validado} />;
    default:
      return <ResumenDeCuenta cuenta={cuenta} />;
  }
}

function ResumenDeCuenta({ cuenta }: { readonly cuenta: Cuenta360 }) {
  const s = cuenta.snapshot;
  const p = cuenta.presencia;
  return (
    <div className="rejilla rejilla--dos">
      <section className="tarjeta" aria-labelledby="cuenta-corte">
        <h2 id="cuenta-corte">En el corte del {formatearFecha(cuenta.fecha_corte)}</h2>
        {s === null ? (
          <p>
            <InsigniaEstado estado="SIN_RESULTADO" /> El corte del contexto no trae esta cuenta.
          </p>
        ) : (
          <Ficha
            etiqueta="Snapshot del corte"
            filas={[
              ["Producto", s.producto],
              ["Saldo total", formatearMonto(s.saldo_total)],
              [
                "Saldo atrasado",
                <ValorOpcional key="sa" valor={s.saldo_atrasado} formatear={formatearMonto} />,
              ],
              ["Días de atraso", formatearEntero(s.dias_atraso)],
              [
                "Atraso máximo",
                <ValorOpcional key="am" valor={s.atraso_maximo} formatear={formatearEntero} />,
              ],
              ["Canal de la fuente", s.canal],
              ["Estrategia de la fuente", s.estrategia ?? "—"],
              ["Entidad y municipio (fuente)", `${s.cve_entidad} · ${s.cve_municipio}`],
            ]}
          />
        )}
      </section>
      <section className="tarjeta" aria-labelledby="cuenta-presencia">
        <h2 id="cuenta-presencia">Presencia a la fecha del corte</h2>
        <Ficha
          etiqueta="Presencia"
          filas={[
            ["Estado", <InsigniaEstado key="e" estado={p.estado_presencia} />],
            [
              "Primera observación",
              p.primera_observacion === null ? "—" : formatearFecha(p.primera_observacion),
            ],
            [
              "Última observación",
              p.ultima_observacion === null ? "—" : formatearFecha(p.ultima_observacion),
            ],
            ["Cortes observados", formatearEntero(p.cortes_observados)],
            ["Cortes ausentes", formatearEntero(p.cortes_ausentes_desde_primera_observacion)],
            [
              "Salidas y reingresos",
              `${formatearEntero(p.salidas_observadas)} · ${formatearEntero(p.reingresos_observados)}`,
            ],
            ["Pagos observados", formatearEntero(p.pagos_observados)],
          ]}
        />
        <p className="nota">
          Que una cuenta no aparezca en un corte no dice si se liquidó, se canceló o se vendió.
        </p>
      </section>
      <section className="tarjeta tarjeta--ancha" aria-labelledby="cuenta-secciones">
        <h2 id="cuenta-secciones">Lo que publicó cada ejecución del contexto</h2>
        <Ficha
          etiqueta="Secciones"
          filas={[
            [
              "Decisión",
              cuenta.decision.estado === "DISPONIBLE" ? (
                <span key="d">
                  {etiquetaDeCodigo(cuenta.decision.prioridad_final ?? "")} ·{" "}
                  {cuenta.decision.canal_recomendado}
                </span>
              ) : (
                <InsigniaEstado key="d" estado={cuenta.decision.estado} />
              ),
            ],
            [
              "Geografía y base",
              cuenta.geografia.estado === "DISPONIBLE" ? (
                <span key="g">
                  {etiquetaDeCodigo(cuenta.geografia.estado_geografia ?? "")} ·{" "}
                  {cuenta.geografia.codigo_base ?? "sin base"}
                </span>
              ) : (
                <InsigniaEstado key="g" estado={cuenta.geografia.estado} />
              ),
            ],
            [
              "Campo",
              cuenta.campo.asignacion ? (
                <InsigniaEstado key="c" estado={cuenta.campo.asignacion.estado} />
              ) : (
                <InsigniaEstado key="c" estado={cuenta.campo.estado} />
              ),
            ],
            [
              "Ruteo",
              cuenta.ruteo.parada ? (
                <InsigniaEstado key="r" estado={cuenta.ruteo.parada.estado} />
              ) : (
                <InsigniaEstado key="r" estado={cuenta.ruteo.estado} />
              ),
            ],
            [
              "Predicción",
              cuenta.predictivo.prediccion ? (
                <span key="p">
                  p recuperación{" "}
                  {formatearPorcentaje(cuenta.predictivo.prediccion.p_recuperacion_positiva_30d)}
                </span>
              ) : (
                <InsigniaEstado key="p" estado={cuenta.predictivo.estado} />
              ),
            ],
            [
              "Optimización",
              cuenta.optimizacion.resultado ? (
                <InsigniaEstado key="o" estado={cuenta.optimizacion.resultado.estado} />
              ) : (
                <InsigniaEstado key="o" estado={cuenta.optimizacion.estado} />
              ),
            ],
          ]}
        />
        <Aviso tono="aviso">{AVISO_PREDICCION}</Aviso>
        <Aviso tono="aviso">{AVISO_OPTIMO}</Aviso>
      </section>
    </div>
  );
}

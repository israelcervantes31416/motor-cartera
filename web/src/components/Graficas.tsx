/**
 * Las graficas de la app, sobre Recharts, con una sola gramatica: una figura con su titulo y su
 * descripcion, la grafica, y su tabla equivalente detras de "Ver datos" (la grafica nunca es la
 * unica forma de leer un valor). Las marcas son delgadas (barras de 24 px como maximo con el
 * extremo redondeado, lineas de 2 px), la rejilla es una linea solida tenue y el texto usa los
 * colores de texto, nunca el de la serie. La paleta categorica y la rampa ordinal estan validadas
 * (docs/frontend.md).
 *
 * Una grafica no calcula nada: dibuja lo que recibe. Un valor suprimido o no disponible es un hueco,
 * nunca un cero.
 */
import { useId, type ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  Rectangle,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  type BarShapeProps,
} from "recharts";

import { formatearEntero } from "../lib/formato";

export const COLORES = {
  serie: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
  ordinal: ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"],
  superficie: "#fcfcfb",
  rejilla: "#e1e0d9",
  eje: "#c3c2b7",
  tinta: "#0b0b0b",
  tintaSecundaria: "#52514e",
  tintaTenue: "#898781",
} as const;

/** Cuantas categorias caben en una grafica de categorias; con mas, solo la tabla. */
export const MAXIMO_DE_CATEGORIAS = 20;
/** Cuantas partes caben en una composicion: la paleta categorica validada tiene ocho. */
export const MAXIMO_DE_PARTES = 7;

const EJE = {
  tick: { fill: COLORES.tintaSecundaria, fontSize: 12 },
  axisLine: { stroke: COLORES.eje },
  tickLine: false,
} as const;

const TOOLTIP = {
  contentStyle: {
    borderRadius: 6,
    border: `1px solid ${COLORES.rejilla}`,
    color: COLORES.tinta,
    fontSize: 13,
  },
  cursor: { fill: "rgba(42, 120, 214, 0.08)" },
} as const;

export function Figura({
  titulo,
  descripcion,
  datos,
  children,
}: {
  readonly titulo: string;
  readonly descripcion: ReactNode;
  /** La tabla equivalente: lo mismo que la grafica, accesible. */
  readonly datos: ReactNode;
  readonly children: ReactNode;
}) {
  const id = useId();
  return (
    <figure className="figura" aria-labelledby={`${id}-t`} aria-describedby={`${id}-d`}>
      <figcaption>
        <span className="figura__titulo" id={`${id}-t`}>
          {titulo}
        </span>
        <span className="figura__descripcion" id={`${id}-d`}>
          {descripcion}
        </span>
      </figcaption>
      <div className="figura__lienzo">{children}</div>
      <details className="figura__datos">
        <summary>Ver datos</summary>
        {datos}
      </details>
    </figure>
  );
}

export interface Punto {
  readonly etiqueta: string;
  /** null es un hueco en la linea: un periodo suprimido o sin dato, nunca un cero. */
  readonly valor: number | null;
}

export function SerieTemporal({
  puntos,
  formatear,
  titulo,
  alto = 260,
}: {
  readonly puntos: readonly Punto[];
  readonly formatear: (valor: number) => string;
  readonly titulo: string;
  readonly alto?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={alto}>
      <LineChart
        data={[...puntos]}
        title={titulo}
        margin={{ top: 12, right: 16, bottom: 4, left: 4 }}
      >
        <CartesianGrid stroke={COLORES.rejilla} vertical={false} />
        <XAxis dataKey="etiqueta" {...EJE} minTickGap={24} />
        <YAxis {...EJE} width={72} tickFormatter={formatear} />
        <Tooltip
          {...TOOLTIP}
          formatter={(valor) => (typeof valor === "number" ? formatear(valor) : String(valor))}
        />
        <Line
          type="monotone"
          dataKey="valor"
          name={titulo}
          stroke={COLORES.serie[0]}
          strokeWidth={2}
          dot={{ r: 4, strokeWidth: 2, stroke: COLORES.superficie, fill: COLORES.serie[0] }}
          activeDot={{ r: 6, strokeWidth: 2, stroke: COLORES.superficie }}
          connectNulls={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

export interface Barra {
  readonly categoria: string;
  readonly valor: number;
}

/** Una sola serie de barras horizontales: un color, el primero de la paleta, para todas. */
export function Barras({
  barras,
  formatear,
  titulo,
  colores,
}: {
  readonly barras: readonly Barra[];
  readonly formatear: (valor: number) => string;
  readonly titulo: string;
  /** Solo para categorias ordenadas (etapas de un embudo): la rampa ordinal. */
  readonly colores?: readonly string[];
}) {
  const alto = Math.max(120, barras.length * 36 + 48);
  return (
    <ResponsiveContainer width="100%" height={alto}>
      <BarChart
        data={[...barras]}
        title={titulo}
        layout="vertical"
        margin={{ top: 8, right: 96, bottom: 4, left: 8 }}
        barCategoryGap={8}
      >
        <CartesianGrid stroke={COLORES.rejilla} horizontal={false} />
        <XAxis type="number" {...EJE} tickFormatter={formatear} />
        <YAxis type="category" dataKey="categoria" {...EJE} width={132} />
        <Tooltip
          {...TOOLTIP}
          formatter={(valor) => (typeof valor === "number" ? formatear(valor) : String(valor))}
        />
        <Bar
          dataKey="valor"
          name={titulo}
          fill={COLORES.serie[0]}
          maxBarSize={24}
          radius={[0, 4, 4, 0]}
          isAnimationActive={false}
          label={{
            position: "right",
            fill: COLORES.tintaSecundaria,
            fontSize: 12,
            formatter: (valor: unknown) => (typeof valor === "number" ? formatear(valor) : ""),
          }}
          {...(colores !== undefined
            ? {
                shape: (props: BarShapeProps) => (
                  <Rectangle
                    {...props}
                    fill={colores[Math.min(props.index, colores.length - 1)] ?? COLORES.serie[0]}
                  />
                ),
              }
            : {})}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Una composicion: una barra apilada con una parte por categoria, cada una con su color fijo en
 * el orden de la paleta, con su leyenda. Muestra los valores tal como se publicaron, sin
 * convertirlos en porcentajes. */
export function Composicion({
  partes,
  formatear,
  titulo,
}: {
  readonly partes: readonly Barra[];
  readonly formatear: (valor: number) => string;
  readonly titulo: string;
}) {
  const fila: Record<string, string | number> = { total: titulo };
  for (const parte of partes) fila[parte.categoria] = parte.valor;
  return (
    <ResponsiveContainer width="100%" height={140}>
      <BarChart
        data={[fila]}
        title={titulo}
        layout="vertical"
        margin={{ top: 4, right: 16, bottom: 4, left: 8 }}
      >
        <XAxis type="number" {...EJE} tickFormatter={formatear} />
        <YAxis type="category" dataKey="total" hide />
        <Tooltip
          {...TOOLTIP}
          formatter={(valor) => (typeof valor === "number" ? formatear(valor) : String(valor))}
        />
        <Legend wrapperStyle={{ fontSize: 12, color: COLORES.tintaSecundaria }} />
        {partes.map((parte, i) => (
          <Bar
            key={parte.categoria}
            dataKey={parte.categoria}
            stackId="composicion"
            fill={COLORES.serie[i % COLORES.serie.length]}
            stroke={COLORES.superficie}
            strokeWidth={2}
            maxBarSize={28}
            isAnimationActive={false}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Columnas de conteos por intervalo (un histograma ya contado en la base). */
export function Histograma({
  columnas,
  titulo,
  formatear,
}: {
  readonly columnas: readonly Barra[];
  readonly titulo: string;
  readonly formatear: (valor: number) => string;
}) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart
        data={[...columnas]}
        title={titulo}
        margin={{ top: 12, right: 16, bottom: 4, left: 4 }}
      >
        <CartesianGrid stroke={COLORES.rejilla} vertical={false} />
        <XAxis
          dataKey="categoria"
          {...EJE}
          interval="preserveStartEnd"
          minTickGap={8}
          fontSize={11}
        />
        <YAxis {...EJE} width={64} tickFormatter={formatear} />
        <Tooltip
          {...TOOLTIP}
          formatter={(valor) => (typeof valor === "number" ? formatear(valor) : String(valor))}
        />
        <Bar
          dataKey="valor"
          name={titulo}
          fill={COLORES.serie[0]}
          maxBarSize={24}
          radius={[4, 4, 0, 0]}
          isAnimationActive={false}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}

export interface PuntoDeCalibracion {
  readonly prediccion: number;
  readonly observada: number;
}

/** La prediccion media de cada bin contra la frecuencia observada, con la diagonal de la
 * calibracion perfecta: solo los puntos publicados, sin suavizar. */
export function Calibracion({
  puntos,
  titulo,
}: {
  readonly puntos: readonly PuntoDeCalibracion[];
  readonly titulo: string;
}) {
  const porcentaje = (v: number) => `${Math.round(v * 100)} %`;
  return (
    <ResponsiveContainer width="100%" height={300}>
      <ScatterChart title={titulo} margin={{ top: 12, right: 16, bottom: 24, left: 4 }}>
        <CartesianGrid stroke={COLORES.rejilla} />
        <XAxis
          type="number"
          dataKey="prediccion"
          name="Predicción media"
          domain={[0, 1]}
          {...EJE}
          tickFormatter={porcentaje}
          label={{
            value: "Predicción media del bin",
            position: "insideBottom",
            offset: -12,
            fill: COLORES.tintaSecundaria,
            fontSize: 12,
          }}
        />
        <YAxis
          type="number"
          dataKey="observada"
          name="Frecuencia observada"
          domain={[0, 1]}
          {...EJE}
          width={56}
          tickFormatter={porcentaje}
        />
        <Tooltip
          {...TOOLTIP}
          formatter={(valor) =>
            typeof valor === "number" ? `${(valor * 100).toFixed(1)} %` : String(valor)
          }
        />
        <Legend wrapperStyle={{ fontSize: 12, color: COLORES.tintaSecundaria }} />
        <ReferenceLine
          segment={[
            { x: 0, y: 0 },
            { x: 1, y: 1 },
          ]}
          stroke={COLORES.tintaTenue}
          strokeWidth={1}
          ifOverflow="extendDomain"
          label={{ value: "Calibración perfecta", fill: COLORES.tintaSecundaria, fontSize: 11 }}
        />
        <Scatter
          name="Bins de TEST"
          data={[...puntos]}
          fill={COLORES.serie[0]}
          stroke={COLORES.superficie}
          strokeWidth={2}
          line={{ stroke: COLORES.serie[0], strokeWidth: 2 }}
          isAnimationActive={false}
        />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

/** El decimo i de [0, 1], legible: 0 es "0–10 %". */
export function decimo(i: number): string {
  return `${i * 10}–${(i + 1) * 10} %`;
}

export function TablaHistograma({ cuentas }: { readonly cuentas: readonly number[] }) {
  return (
    <table className="tabla tabla--compacta">
      <caption>Predicciones por décimo</caption>
      <thead>
        <tr>
          <th scope="col">Intervalo</th>
          <th scope="col" className="tabla__numerica">
            Predicciones
          </th>
        </tr>
      </thead>
      <tbody>
        {cuentas.map((n, i) => (
          <tr key={i}>
            <th scope="row">{decimo(i)}</th>
            <td className="tabla__numerica">{formatearEntero(n)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

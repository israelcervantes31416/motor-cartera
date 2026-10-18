/**
 * Pestanas de una pagina: enlaces, no estado de React. Cada pestana es su propia URL (una ruta o un
 * parametro), asi que se puede copiar, volver atras y abrir en otra ventana. La activa lleva
 * aria-current="page".
 */
import { NavLink } from "react-router";

export interface Pestana {
  readonly etiqueta: string;
  readonly a: string;
}

export function Pestanas({
  pestanas,
  etiqueta,
}: {
  readonly pestanas: readonly Pestana[];
  readonly etiqueta: string;
}) {
  return (
    <nav className="pestanas" aria-label={etiqueta}>
      <ul>
        {pestanas.map((p) => (
          <li key={p.etiqueta}>
            <NavLink
              to={p.a}
              end
              className={({ isActive }) => (isActive ? "pestana pestana--activa" : "pestana")}
            >
              {p.etiqueta}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

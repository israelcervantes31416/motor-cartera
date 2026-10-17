// Los presupuestos del bundle, fijados antes de medir (docs/frontend.md), contra dist/:
//
//   - el shell inicial (el JS y el CSS que index.html carga de entrada): 250 KiB gzip como maximo;
//   - ningun chunk diferido de JS: mas de 350 KiB gzip;
//   - todo el JS: 1.2 MiB gzip como maximo.
//
// Mide con zlib (nivel 9), sin dependencias, y sale con 1 si uno se rompe. Correr despues de
// `npm run build`.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";

const KIB = 1024;
const PRESUPUESTOS = { shell: 250 * KIB, chunk: 350 * KIB, total: 1.2 * 1024 * KIB };
const DIST = new URL("../dist/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");

function gzip(ruta) {
  return gzipSync(readFileSync(ruta), { level: 9 }).length;
}

const html = readFileSync(join(DIST, "index.html"), "utf8");
const iniciales = new Set(
  [...html.matchAll(/(?:src|href)="\/(assets\/[^"]+\.(?:js|css))"/g)].map((m) => m[1]),
);
const archivos = readdirSync(join(DIST, "assets"))
  .filter((nombre) => nombre.endsWith(".js") || nombre.endsWith(".css"))
  .map((nombre) => {
    const ruta = join(DIST, "assets", nombre);
    return {
      nombre: `assets/${nombre}`,
      bytes: statSync(ruta).size,
      gzip: gzip(ruta),
      inicial: iniciales.has(`assets/${nombre}`),
      js: nombre.endsWith(".js"),
    };
  })
  .sort((a, b) => b.gzip - a.gzip);

const kib = (n) => `${(n / KIB).toFixed(1)} KiB`;
const shell = archivos.filter((a) => a.inicial).reduce((s, a) => s + a.gzip, 0);
const totalJs = archivos.filter((a) => a.js).reduce((s, a) => s + a.gzip, 0);
const diferidos = archivos.filter((a) => a.js && !a.inicial);
const mayor = diferidos[0];

console.log("| Archivo | Inicial | Bytes | gzip |");
console.log("| --- | --- | ---: | ---: |");
for (const a of archivos) {
  console.log(`| ${a.nombre} | ${a.inicial ? "sí" : "no"} | ${a.bytes} | ${kib(a.gzip)} |`);
}
console.log();
const resultados = [
  ["Shell inicial (JS + CSS)", shell, PRESUPUESTOS.shell],
  [
    `Mayor chunk diferido${mayor ? ` (${mayor.nombre})` : ""}`,
    mayor?.gzip ?? 0,
    PRESUPUESTOS.chunk,
  ],
  ["Todo el JS", totalJs, PRESUPUESTOS.total],
];
let roto = false;
for (const [nombre, medido, limite] of resultados) {
  const ok = medido <= limite;
  roto ||= !ok;
  console.log(`${ok ? "ok " : "MAL"} ${nombre}: ${kib(medido)} de ${kib(limite)} gzip`);
}
if (roto) {
  console.error("Un presupuesto del bundle se rompió: optimizar, no subir el presupuesto.");
  process.exit(1);
}

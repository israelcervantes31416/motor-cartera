# Política de seguridad

## Versiones soportadas

Las correcciones de seguridad salen sobre la última versión menor de 1.x, como un parche (o un menor,
si agregan algo), salvo que su aviso diga otra cosa de forma explícita. Qué puede cambiar una
corrección de seguridad, incluso si rompe la compatibilidad, lo dice la
[política de versionado](../docs/versioning.md#la-seguridad).

| Versión | Soportada |
|---|---|
| La última menor de 1.x (hoy, v1.0) | Sí |
| Las menores anteriores de 1.x | No: se actualiza a la última, sin romper nada |
| 0.x | No |

## Cómo reportar una vulnerabilidad

Por el **reporte privado de vulnerabilidades de GitHub**: en la pestaña *Security* del repositorio,
*Report a vulnerability*, o directamente en
<https://github.com/israelcervantes31416/motor-cartera/security/advisories/new>. El reporte queda
privado entre quien lo envía y el mantenedor hasta que se publique su aviso.

**No abras un issue, un pull request ni una discusión pública** con los detalles de una
vulnerabilidad o un exploit: lo hace público antes de que exista una corrección.

Un reporte útil trae:

- qué componente afecta (la API, el worker, la app web, el edge, el compose de producción, la
  referencia de la nube, el CI) y en qué versión o commit;
- los pasos para reproducirlo, con la configuración necesaria;
- el impacto que esperas (qué se lee, se modifica o se evita que no debería);
- si conoces una mitigación.

No incluyas datos reales de personas ni credenciales en el reporte: el proyecto solo usa datos
sintéticos, y una prueba de concepto no los necesita.

## Qué esperar

Este es un proyecto personal, mantenido por una persona: no hay un compromiso de tiempo de
respuesta. Cada reporte se lee, se reproduce y se responde por el mismo canal privado; si se
confirma, se corrige en una versión nueva y se publica su aviso, con el crédito de quien lo reportó
si lo desea.

## Alcance

Entra el código de este repositorio y lo que despliega: la API, el worker, la app web y su proxy,
el compose de producción (el edge, la identidad, la observabilidad y el respaldo), la referencia de
Google Cloud en OpenTofu y el CI. No hay un despliegue público de motor-cartera: no pruebes contra
sistemas de terceros. Las vulnerabilidades de una dependencia se reportan a su proyecto; aquí,
cómo afectan a motor-cartera.

## La arquitectura de seguridad

Los controles (identidad, permisos, credenciales, endurecimiento y configuración que falla cerrada)
están en [docs/security.md](../docs/security.md); el modelo de amenazas, en
[docs/threat-model.md](../docs/threat-model.md); la cadena de suministro, en
[docs/cadena-de-suministro.md](../docs/cadena-de-suministro.md).

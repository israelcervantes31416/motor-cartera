"""Las fuentes oficiales: la evidencia de cada ingesta y lo que se construye directo sobre ella.

Un archivo que llega es evidencia antes que datos. Este paquete lo conserva tal como llego y lo
lleva, por capas, hasta lo que consumen los motores:

    SOURCE EVIDENCE      el archivo original, byte por byte, en un almacen por contenido
            |
    SOURCE-CONFORMED     los registros validos de cartera/v2 o pagos/v1, normalizados al
            |            contrato, en Parquet
    OPERATIONAL          la proyeccion de cartera/v2 a Cuenta, que es lo que leen
    PROJECTION           decision/v1, territorial/v1 y ruteo/v1

- `almacen`: el almacen de artefactos por contenido (SourceArtifactStore).
- `formatos`: reconocer un formato por sus bytes y no solo por la extension.
- `artefactos`: registrar en la base cada artefacto guardado.
- `geografia`: el catalogo publico del INEGI con que la proyeccion resuelve entidad y municipio.

No reexporta nada: cada modulo se importa por su nombre.
"""

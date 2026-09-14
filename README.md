# motor-cartera

Motor de ingesta, validación y segmentación de cartera de crédito al consumo, construido
sobre **datos sintéticos**.

## De qué se trata

Una cartera de crédito llega en archivos de sistemas distintos, con encabezados que cambian
sin aviso, duplicados, saldos imposibles y claves geográficas incompletas. Sobre esos
archivos alguien tiene que decidir a quién contactar, por qué canal y en qué orden. Si el
dato entra mal, la decisión sale mal, y la decisión se ejecuta.

Este proyecto resuelve ese problema de punta a punta: **lee, valida contra un contrato,
persiste con trazabilidad y segmenta**. La regla de diseño es *fail-closed*: si los datos
no cumplen el contrato, el proceso se detiene y no escribe nada. Es preferible no producir
salida a producir salida incorrecta.

## Datos

**Ningún dato real entra a este repositorio.** Todo lo que el sistema procesa lo produce el
generador sintético incluido. Las claves geográficas usan el catálogo público del INEGI;
el resto son datos inventados con distribuciones parecidas a las reales.

## Estado

Fase 1 en construcción.

- [x] Estructura, Docker, PostgreSQL, migraciones y CI
- [x] Contrato de datos con validación *fail-closed*
- [ ] Generador de cartera sintética
- [ ] Lectores de Excel, CSV y ZIP con detección flexible de columnas
- [ ] Persistencia con trazabilidad por corrida
- [ ] API con FastAPI
- [ ] Motor de segmentación
- [ ] Motor territorial: agrupamiento y ruteo

## Cómo correrlo

```bash
cp .env.example .env
docker compose up -d postgres
uv pip install -e ".[dev]"
alembic upgrade head
pytest
```

## Arquitectura

```
src/motor_cartera/
├── config.py          Configuración desde el entorno
├── contratos/         Qué forma deben tener los datos para poder confiar en ellos
├── db/                Modelo persistente: Corrida y Cuenta
├── generador/         Cartera sintética, único origen de datos del proyecto
├── ingesta/           Lectura de archivos crudos y mapeo a nombres canónicos
└── cli.py             Comandos
```

Todo lo que se escribe cuelga de una **Corrida**. Si alguien pregunta de dónde salió un
número, la respuesta es una fila de esa tabla.

## Licencia

MIT

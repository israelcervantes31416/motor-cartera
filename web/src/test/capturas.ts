// Generado por scripts/capturar_fixtures_web.py: respuestas de la API local sobre la
// base sintetica de la prueba de humo, recortadas. No son datos reales y no se edita
// a mano: cada constante esta tipada por el contrato de la web.
import type { Respuesta } from "../api/cliente";

export const opcionesSinCorte: Respuesta<"/app/v1/contextos/opciones"> = {
  "aviso": "app/v1 es de solo lectura: compone lo que cada motor ya publico, bajo un contexto explicito (el corte y cada ejecucion, por su identificador publico). No ejecuta, no envia, no llama, no visita, no optimiza ni reentrena nada, y nada se elige como 'lo ultimo'.",
  "cartera_id": "CARTERA_PRINCIPAL",
  "carteras": [
    {
      "cartera_id": "CARTERA_PRINCIPAL",
      "cortes": 5,
      "ultimo_corte": "2026-10-28"
    },
    {
      "cartera_id": "PREDICTIVO",
      "cortes": 20,
      "ultimo_corte": "2026-07-15"
    }
  ],
  "corte": null,
  "cortes": [
    {
      "corte_id": "34d7b199-3180-51da-98db-c61215c10913",
      "creado_en": "2026-10-17T13:14:23.771960Z",
      "cuentas": 974,
      "fecha_corte": "2026-10-28"
    },
    {
      "corte_id": "df3b22d9-d4ef-515f-8ebd-f8eb7df0823a",
      "creado_en": "2026-10-17T13:14:23.883406Z",
      "cuentas": 987,
      "fecha_corte": "2026-10-21"
    },
    {
      "corte_id": "e524f402-f5c9-5484-87eb-e9ea09f5e3ca",
      "creado_en": "2026-10-17T13:14:23.981472Z",
      "cuentas": 1003,
      "fecha_corte": "2026-10-14"
    },
    {
      "corte_id": "d60c035f-7afe-5dc4-a871-655365c43194",
      "creado_en": "2026-10-17T13:14:24.082223Z",
      "cuentas": 1000,
      "fecha_corte": "2026-10-07"
    },
    {
      "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "creado_en": "2026-10-17T13:14:16.920183Z",
      "cuentas": 1000,
      "fecha_corte": "2026-09-30"
    }
  ],
  "despacho_id": "DSP_001",
  "ejecuciones": [],
  "pagina": 1,
  "por_pagina": 5,
  "sugerencias": [],
  "total": 5
};

export const opciones: Respuesta<"/app/v1/contextos/opciones"> = {
  "aviso": "app/v1 es de solo lectura: compone lo que cada motor ya publico, bajo un contexto explicito (el corte y cada ejecucion, por su identificador publico). No ejecuta, no envia, no llama, no visita, no optimiza ni reentrena nada, y nada se elige como 'lo ultimo'.",
  "cartera_id": "CARTERA_PRINCIPAL",
  "carteras": [
    {
      "cartera_id": "CARTERA_PRINCIPAL",
      "cortes": 5,
      "ultimo_corte": "2026-10-28"
    },
    {
      "cartera_id": "PREDICTIVO",
      "cortes": 20,
      "ultimo_corte": "2026-07-15"
    }
  ],
  "corte": {
    "cartera_id": "CARTERA_PRINCIPAL",
    "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
    "creado_en": "2026-10-17T13:14:16.920183Z",
    "cuentas": 1000,
    "dataset_id": "3eb2363e-c122-4eb5-abd3-ac9678aa43e8",
    "despacho_id": "DSP_001",
    "fecha_corte": "2026-09-30",
    "firma_contenido": "a5c54444360efe7c1619339ec5cf6ceff19c1917ed38bccf39b042a587f2def5",
    "run_id": "50b764f7-ddd2-459d-8bd4-8756df8c32b6",
    "version_modelo": "historia/v1"
  },
  "cortes": [
    {
      "corte_id": "34d7b199-3180-51da-98db-c61215c10913",
      "creado_en": "2026-10-17T13:14:23.771960Z",
      "cuentas": 974,
      "fecha_corte": "2026-10-28"
    },
    {
      "corte_id": "df3b22d9-d4ef-515f-8ebd-f8eb7df0823a",
      "creado_en": "2026-10-17T13:14:23.883406Z",
      "cuentas": 987,
      "fecha_corte": "2026-10-21"
    },
    {
      "corte_id": "e524f402-f5c9-5484-87eb-e9ea09f5e3ca",
      "creado_en": "2026-10-17T13:14:23.981472Z",
      "cuentas": 1003,
      "fecha_corte": "2026-10-14"
    },
    {
      "corte_id": "d60c035f-7afe-5dc4-a871-655365c43194",
      "creado_en": "2026-10-17T13:14:24.082223Z",
      "cuentas": 1000,
      "fecha_corte": "2026-10-07"
    },
    {
      "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "creado_en": "2026-10-17T13:14:16.920183Z",
      "cuentas": 1000,
      "fecha_corte": "2026-09-30"
    }
  ],
  "despacho_id": "DSP_001",
  "ejecuciones": [
    {
      "elementos": [
        {
          "as_of": "2026-09-30",
          "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
          "estado": "EXITOSA",
          "fecha_corte": "2026-09-30",
          "fecha_operacion": null,
          "firma_inputs": "b30987e15bb8cec05f12bc4019393cd5386e53224566dd7fa8c24fa908e8036d",
          "hash_politica": "2f9687400863f6af49a3c0fc51fe10c6c3b6f080804fcd4ef643ddad641d29e3",
          "iniciada_en": "2026-10-17T13:14:29.506615Z",
          "known_at": "2026-10-17T13:14:29.444442Z",
          "lee": {},
          "resultado": "DECISION_PUBLICADA",
          "run_id": "13c166a9-218f-435d-a36c-5aea9eaee03e",
          "terminada_en": "2026-10-17T13:14:30.301800Z",
          "tipo": "DECISION_V2",
          "version_motor": "decision/v2",
          "version_politica": "decision-policy/public-v1",
          "zona_horaria": "America/Mexico_City"
        }
      ],
      "tipo": "DECISION_V2",
      "total": 1
    },
    {
      "elementos": [
        {
          "as_of": "2026-10-01",
          "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
          "estado": "EXITOSA",
          "fecha_corte": "2026-09-30",
          "fecha_operacion": null,
          "firma_inputs": "394a082a72e220a33a0e0da2300d4358e90d37d071e8f487573a8db73b5fa12a",
          "hash_politica": "3529b0893889bf8b28491c7fd2671cd3961b83915ba83084146585351fcb8a80",
          "iniciada_en": "2026-10-17T13:15:41.444118Z",
          "known_at": "2026-10-17T13:15:35.619443Z",
          "lee": {
            "DECISION_V2": "13c166a9-218f-435d-a36c-5aea9eaee03e",
            "EVALUACION_PROMESAS": "d40af884-dfcd-461c-9efb-b2d1cef1930d",
            "GEOGRAFIA": "7455fff1-9fd1-4b8b-a217-cbfd1865f6e4"
          },
          "resultado": "ANALYTICS_PUBLICADA",
          "run_id": "f2c5b154-a3f3-4105-94a0-f14032d00e37",
          "terminada_en": "2026-10-17T13:15:42.502268Z",
          "tipo": "ANALYTICS",
          "version_motor": "analytics/v1",
          "version_politica": "analytics-public-v1",
          "zona_horaria": "America/Mexico_City"
        },
        {
          "as_of": "2026-10-01",
          "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
          "estado": "EXITOSA",
          "fecha_corte": "2026-09-30",
          "fecha_operacion": null,
          "firma_inputs": "74db4ac084c814dc6dfe7d1acc9e8b5c8dd1a785a0da35528ce1e9887d1d674d",
          "hash_politica": "3529b0893889bf8b28491c7fd2671cd3961b83915ba83084146585351fcb8a80",
          "iniciada_en": "2026-10-17T13:14:44.502320Z",
          "known_at": "2026-10-17T13:14:44.193394Z",
          "lee": {
            "CAMPO_V2": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
            "DECISION_V2": "13c166a9-218f-435d-a36c-5aea9eaee03e",
            "EVALUACION_PROMESAS": "d40af884-dfcd-461c-9efb-b2d1cef1930d",
            "GEOGRAFIA": "7455fff1-9fd1-4b8b-a217-cbfd1865f6e4",
            "RUTEO_V2": "4730febb-aa0c-4372-8918-04387d31fe71",
            "TERRITORIAL_V2": "d89b3565-7416-47a0-b4e7-e5bf22bc3e0b"
          },
          "resultado": "ANALYTICS_PUBLICADA",
          "run_id": "2178f91d-00c5-42a3-9aa3-f0527a095378",
          "terminada_en": "2026-10-17T13:14:45.753970Z",
          "tipo": "ANALYTICS",
          "version_motor": "analytics/v1",
          "version_politica": "analytics-public-v1",
          "zona_horaria": "America/Mexico_City"
        }
      ],
      "tipo": "ANALYTICS",
      "total": 2
    },
    {
      "elementos": [
        {
          "as_of": "2026-09-30",
          "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
          "estado": "EXITOSA",
          "fecha_corte": "2026-09-30",
          "fecha_operacion": null,
          "firma_inputs": "7cff352311bff21c8da67f7c8df64ee81ac8182fad3715de4322a1e848a6a3dd",
          "hash_politica": "72630f625de5bc53b777da286395474b857d641b810e792c8cb595159973bfe1",
          "iniciada_en": "2026-10-17T13:16:43.369953Z",
          "known_at": "2026-10-17T13:14:29.444442Z",
          "lee": {
            "MODELO": "261a485b-7f86-44f9-808a-190f164fd471"
          },
          "resultado": "PREDICCIONES_PUBLICADAS",
          "run_id": "c1397868-406c-431b-ae0a-25a791e574ae",
          "terminada_en": "2026-10-17T13:16:43.996389Z",
          "tipo": "SCORING",
          "version_motor": "predictive/model-v1",
          "version_politica": "predictive-training/public-v1",
          "zona_horaria": "America/Mexico_City"
        }
      ],
      "tipo": "SCORING",
      "total": 1
    },
    {
      "elementos": [
        {
          "as_of": null,
          "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
          "estado": "EXITOSA",
          "fecha_corte": "2026-09-30",
          "fecha_operacion": "2026-10-01",
          "firma_inputs": "1526f329dfdd26da67b3ec4c59e3857985e2a1574774f1d03d2a2ec0f676fd77",
          "hash_politica": "540bc21fadd8297c2a8d427e8af540610642016e7dea5e9be87f997c53d23fc5",
          "iniciada_en": "2026-10-17T13:16:44.547155Z",
          "known_at": null,
          "lee": {
            "CAMPO_V2": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
            "CONFIGURACION_OPTIMIZACION": "ec6c6274-a2a0-4999-b2f9-5721c78c5bda",
            "DECISION_V2": "13c166a9-218f-435d-a36c-5aea9eaee03e",
            "RUTEO_V2": "4730febb-aa0c-4372-8918-04387d31fe71",
            "SCORING": "c1397868-406c-431b-ae0a-25a791e574ae"
          },
          "resultado": "PLAN_PUBLICADO",
          "run_id": "bb5fda47-20a1-4bf9-a466-69102e76b965",
          "terminada_en": "2026-10-17T13:16:45.184838Z",
          "tipo": "OPTIMIZACION",
          "version_motor": "optimization/v1",
          "version_politica": "optimization-public-v1",
          "zona_horaria": null
        }
      ],
      "tipo": "OPTIMIZACION",
      "total": 1
    },
    {
      "elementos": [
        {
          "as_of": null,
          "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
          "estado": "EXITOSA",
          "fecha_corte": "2026-09-30",
          "fecha_operacion": "2026-10-01",
          "firma_inputs": "52fdbb54ae8f48a9733e33f1b83813495a8b91b45be19ed89301422da1bc81b7",
          "hash_politica": "b8d51c1441cc93a7b0d22416ac258bb134c70608bfef5ab1e37a6d7c59ecd854",
          "iniciada_en": "2026-10-17T13:14:39.341688Z",
          "known_at": null,
          "lee": {
            "CONFIGURACION_CAMPO": "4cece266-3c7a-4ff1-bc62-d2a64989e9ac",
            "DECISION_V2": "13c166a9-218f-435d-a36c-5aea9eaee03e",
            "GEOGRAFIA": "7455fff1-9fd1-4b8b-a217-cbfd1865f6e4",
            "TERRITORIAL_V2": "d89b3565-7416-47a0-b4e7-e5bf22bc3e0b"
          },
          "resultado": "PLAN_PUBLICADO",
          "run_id": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
          "terminada_en": "2026-10-17T13:14:39.873764Z",
          "tipo": "CAMPO_V2",
          "version_motor": "campo/v2",
          "version_politica": "campo-public-v2",
          "zona_horaria": "America/Mexico_City"
        },
        {
          "as_of": null,
          "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
          "estado": "EXITOSA",
          "fecha_corte": "2026-09-30",
          "fecha_operacion": "2026-10-01",
          "firma_inputs": "2fee98897c1952cbeb80404c77eadc87a60787c81851f5893c3f683884cd4235",
          "hash_politica": "b8d51c1441cc93a7b0d22416ac258bb134c70608bfef5ab1e37a6d7c59ecd854",
          "iniciada_en": "2026-10-17T13:14:36.075712Z",
          "known_at": null,
          "lee": {
            "CONFIGURACION_CAMPO": "6e5889c3-cd47-48ee-a0a2-7fa03428f48a",
            "DECISION_V2": "13c166a9-218f-435d-a36c-5aea9eaee03e",
            "GEOGRAFIA": "7455fff1-9fd1-4b8b-a217-cbfd1865f6e4",
            "TERRITORIAL_V2": "9d15399a-b1f0-4e28-82d2-0dc3b5890d16"
          },
          "resultado": "PLAN_PUBLICADO",
          "run_id": "a66dc9af-fd30-4f50-88ac-508ffe828626",
          "terminada_en": "2026-10-17T13:14:36.404796Z",
          "tipo": "CAMPO_V2",
          "version_motor": "campo/v2",
          "version_politica": "campo-public-v2",
          "zona_horaria": "America/Mexico_City"
        }
      ],
      "tipo": "CAMPO_V2",
      "total": 2
    },
    {
      "elementos": [
        {
          "as_of": null,
          "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
          "estado": "EXITOSA",
          "fecha_corte": "2026-09-30",
          "fecha_operacion": "2026-10-01",
          "firma_inputs": "e1a7f5f8e24faeb78cc4d6abc0dcb3bddf427a9384602399e86c9bffc8443e9e",
          "hash_politica": "6afe7b063d0151adfbac89efe6c49979a9507dc7257175e3fa514c3ebf31b097",
          "iniciada_en": "2026-10-17T13:15:19.742025Z",
          "known_at": null,
          "lee": {
            "CAMPO_V2": "a66dc9af-fd30-4f50-88ac-508ffe828626",
            "RED_VIAL": "84d173b2-0b93-4838-97ac-2af88b18e5fc"
          },
          "resultado": "RUTAS_PUBLICADAS",
          "run_id": "8cc0ffed-5df0-4ff3-80bf-0ce478c18bbc",
          "terminada_en": "2026-10-17T13:15:20.376233Z",
          "tipo": "RUTEO_V2",
          "version_motor": "ruteo/v2",
          "version_politica": "ruteo-public-v2",
          "zona_horaria": "America/Mexico_City"
        },
        {
          "as_of": null,
          "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
          "estado": "EXITOSA",
          "fecha_corte": "2026-09-30",
          "fecha_operacion": "2026-10-01",
          "firma_inputs": "7362dd512045817fd08491b126e8a94a2193bb08b229f531f30e71219dcfdb09",
          "hash_politica": "6afe7b063d0151adfbac89efe6c49979a9507dc7257175e3fa514c3ebf31b097",
          "iniciada_en": "2026-10-17T13:14:40.497369Z",
          "known_at": null,
          "lee": {
            "CAMPO_V2": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
            "RED_VIAL": "84d173b2-0b93-4838-97ac-2af88b18e5fc"
          },
          "resultado": "RUTAS_PUBLICADAS",
          "run_id": "4730febb-aa0c-4372-8918-04387d31fe71",
          "terminada_en": "2026-10-17T13:14:41.037405Z",
          "tipo": "RUTEO_V2",
          "version_motor": "ruteo/v2",
          "version_politica": "ruteo-public-v2",
          "zona_horaria": "America/Mexico_City"
        }
      ],
      "tipo": "RUTEO_V2",
      "total": 2
    }
  ],
  "pagina": 1,
  "por_pagina": 5,
  "sugerencias": [
    {
      "codigo": "DESDE_OPTIMIZACION",
      "contexto": {
        "analytics_run_id": "f2c5b154-a3f3-4105-94a0-f14032d00e37",
        "campo_run_id": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
        "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
        "decision_run_id": "13c166a9-218f-435d-a36c-5aea9eaee03e",
        "optimizacion_run_id": "bb5fda47-20a1-4bf9-a466-69102e76b965",
        "ruteo_run_id": "4730febb-aa0c-4372-8918-04387d31fe71",
        "scoring_run_id": "c1397868-406c-431b-ae0a-25a791e574ae"
      },
      "motivo": "La optimizacion y lo que declaro, con analytics si encadena."
    },
    {
      "codigo": "DESDE_ANALYTICS",
      "contexto": {
        "analytics_run_id": "2178f91d-00c5-42a3-9aa3-f0527a095378",
        "campo_run_id": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
        "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
        "decision_run_id": "13c166a9-218f-435d-a36c-5aea9eaee03e",
        "optimizacion_run_id": "bb5fda47-20a1-4bf9-a466-69102e76b965",
        "ruteo_run_id": "4730febb-aa0c-4372-8918-04387d31fe71",
        "scoring_run_id": "c1397868-406c-431b-ae0a-25a791e574ae"
      },
      "motivo": "La ejecucion de analytics y su manifiesto, con optimizacion si encadena."
    }
  ],
  "total": 5
};

export const validado: Respuesta<"/app/v1/contextos/validar"> = {
  "aviso": "app/v1 es de solo lectura: compone lo que cada motor ya publico, bajo un contexto explicito (el corte y cada ejecucion, por su identificador publico). No ejecuta, no envia, no llama, no visita, no optimiza ni reentrena nada, y nada se elige como 'lo ultimo'.",
  "compatible": true,
  "corte": {
    "cartera_id": "CARTERA_PRINCIPAL",
    "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
    "creado_en": "2026-10-17T13:14:16.920183Z",
    "cuentas": 1000,
    "dataset_id": "3eb2363e-c122-4eb5-abd3-ac9678aa43e8",
    "despacho_id": "DSP_001",
    "fecha_corte": "2026-09-30",
    "firma_contenido": "a5c54444360efe7c1619339ec5cf6ceff19c1917ed38bccf39b042a587f2def5",
    "run_id": "50b764f7-ddd2-459d-8bd4-8756df8c32b6",
    "version_modelo": "historia/v1"
  },
  "declaradas": {
    "ANALYTICS": "f2c5b154-a3f3-4105-94a0-f14032d00e37",
    "CAMPO_V2": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
    "DECISION_V2": "13c166a9-218f-435d-a36c-5aea9eaee03e",
    "OPTIMIZACION": "bb5fda47-20a1-4bf9-a466-69102e76b965",
    "RUTEO_V2": "4730febb-aa0c-4372-8918-04387d31fe71",
    "SCORING": "c1397868-406c-431b-ae0a-25a791e574ae"
  },
  "derivadas": {
    "CONFIGURACION_CAMPO": "4cece266-3c7a-4ff1-bc62-d2a64989e9ac",
    "CONFIGURACION_OPTIMIZACION": "ec6c6274-a2a0-4999-b2f9-5721c78c5bda",
    "EVALUACION_PROMESAS": "d40af884-dfcd-461c-9efb-b2d1cef1930d",
    "GEOGRAFIA": "7455fff1-9fd1-4b8b-a217-cbfd1865f6e4",
    "MODELO": "261a485b-7f86-44f9-808a-190f164fd471",
    "RED_VIAL": "84d173b2-0b93-4838-97ac-2af88b18e5fc",
    "TERRITORIAL_V2": "d89b3565-7416-47a0-b4e7-e5bf22bc3e0b"
  },
  "ejecuciones": [
    {
      "as_of": "2026-09-30",
      "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "estado": "EXITOSA",
      "fecha_corte": "2026-09-30",
      "fecha_operacion": null,
      "firma_inputs": "b30987e15bb8cec05f12bc4019393cd5386e53224566dd7fa8c24fa908e8036d",
      "hash_politica": "2f9687400863f6af49a3c0fc51fe10c6c3b6f080804fcd4ef643ddad641d29e3",
      "iniciada_en": "2026-10-17T13:14:29.506615Z",
      "known_at": "2026-10-17T13:14:29.444442Z",
      "lee": {},
      "resultado": "DECISION_PUBLICADA",
      "run_id": "13c166a9-218f-435d-a36c-5aea9eaee03e",
      "terminada_en": "2026-10-17T13:14:30.301800Z",
      "tipo": "DECISION_V2",
      "version_motor": "decision/v2",
      "version_politica": "decision-policy/public-v1",
      "zona_horaria": "America/Mexico_City"
    },
    {
      "as_of": "2026-10-01",
      "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "estado": "EXITOSA",
      "fecha_corte": "2026-09-30",
      "fecha_operacion": null,
      "firma_inputs": "394a082a72e220a33a0e0da2300d4358e90d37d071e8f487573a8db73b5fa12a",
      "hash_politica": "3529b0893889bf8b28491c7fd2671cd3961b83915ba83084146585351fcb8a80",
      "iniciada_en": "2026-10-17T13:15:41.444118Z",
      "known_at": "2026-10-17T13:15:35.619443Z",
      "lee": {
        "DECISION_V2": "13c166a9-218f-435d-a36c-5aea9eaee03e",
        "EVALUACION_PROMESAS": "d40af884-dfcd-461c-9efb-b2d1cef1930d",
        "GEOGRAFIA": "7455fff1-9fd1-4b8b-a217-cbfd1865f6e4"
      },
      "resultado": "ANALYTICS_PUBLICADA",
      "run_id": "f2c5b154-a3f3-4105-94a0-f14032d00e37",
      "terminada_en": "2026-10-17T13:15:42.502268Z",
      "tipo": "ANALYTICS",
      "version_motor": "analytics/v1",
      "version_politica": "analytics-public-v1",
      "zona_horaria": "America/Mexico_City"
    },
    {
      "as_of": "2026-09-30",
      "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "estado": "EXITOSA",
      "fecha_corte": "2026-09-30",
      "fecha_operacion": null,
      "firma_inputs": "7cff352311bff21c8da67f7c8df64ee81ac8182fad3715de4322a1e848a6a3dd",
      "hash_politica": "72630f625de5bc53b777da286395474b857d641b810e792c8cb595159973bfe1",
      "iniciada_en": "2026-10-17T13:16:43.369953Z",
      "known_at": "2026-10-17T13:14:29.444442Z",
      "lee": {
        "MODELO": "261a485b-7f86-44f9-808a-190f164fd471"
      },
      "resultado": "PREDICCIONES_PUBLICADAS",
      "run_id": "c1397868-406c-431b-ae0a-25a791e574ae",
      "terminada_en": "2026-10-17T13:16:43.996389Z",
      "tipo": "SCORING",
      "version_motor": "predictive/model-v1",
      "version_politica": "predictive-training/public-v1",
      "zona_horaria": "America/Mexico_City"
    },
    {
      "as_of": null,
      "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "estado": "EXITOSA",
      "fecha_corte": "2026-09-30",
      "fecha_operacion": "2026-10-01",
      "firma_inputs": "1526f329dfdd26da67b3ec4c59e3857985e2a1574774f1d03d2a2ec0f676fd77",
      "hash_politica": "540bc21fadd8297c2a8d427e8af540610642016e7dea5e9be87f997c53d23fc5",
      "iniciada_en": "2026-10-17T13:16:44.547155Z",
      "known_at": null,
      "lee": {
        "CAMPO_V2": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
        "CONFIGURACION_OPTIMIZACION": "ec6c6274-a2a0-4999-b2f9-5721c78c5bda",
        "DECISION_V2": "13c166a9-218f-435d-a36c-5aea9eaee03e",
        "RUTEO_V2": "4730febb-aa0c-4372-8918-04387d31fe71",
        "SCORING": "c1397868-406c-431b-ae0a-25a791e574ae"
      },
      "resultado": "PLAN_PUBLICADO",
      "run_id": "bb5fda47-20a1-4bf9-a466-69102e76b965",
      "terminada_en": "2026-10-17T13:16:45.184838Z",
      "tipo": "OPTIMIZACION",
      "version_motor": "optimization/v1",
      "version_politica": "optimization-public-v1",
      "zona_horaria": null
    },
    {
      "as_of": null,
      "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "estado": "EXITOSA",
      "fecha_corte": "2026-09-30",
      "fecha_operacion": "2026-10-01",
      "firma_inputs": "52fdbb54ae8f48a9733e33f1b83813495a8b91b45be19ed89301422da1bc81b7",
      "hash_politica": "b8d51c1441cc93a7b0d22416ac258bb134c70608bfef5ab1e37a6d7c59ecd854",
      "iniciada_en": "2026-10-17T13:14:39.341688Z",
      "known_at": null,
      "lee": {
        "CONFIGURACION_CAMPO": "4cece266-3c7a-4ff1-bc62-d2a64989e9ac",
        "DECISION_V2": "13c166a9-218f-435d-a36c-5aea9eaee03e",
        "GEOGRAFIA": "7455fff1-9fd1-4b8b-a217-cbfd1865f6e4",
        "TERRITORIAL_V2": "d89b3565-7416-47a0-b4e7-e5bf22bc3e0b"
      },
      "resultado": "PLAN_PUBLICADO",
      "run_id": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
      "terminada_en": "2026-10-17T13:14:39.873764Z",
      "tipo": "CAMPO_V2",
      "version_motor": "campo/v2",
      "version_politica": "campo-public-v2",
      "zona_horaria": "America/Mexico_City"
    },
    {
      "as_of": null,
      "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "estado": "EXITOSA",
      "fecha_corte": "2026-09-30",
      "fecha_operacion": "2026-10-01",
      "firma_inputs": "7362dd512045817fd08491b126e8a94a2193bb08b229f531f30e71219dcfdb09",
      "hash_politica": "6afe7b063d0151adfbac89efe6c49979a9507dc7257175e3fa514c3ebf31b097",
      "iniciada_en": "2026-10-17T13:14:40.497369Z",
      "known_at": null,
      "lee": {
        "CAMPO_V2": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
        "RED_VIAL": "84d173b2-0b93-4838-97ac-2af88b18e5fc"
      },
      "resultado": "RUTAS_PUBLICADAS",
      "run_id": "4730febb-aa0c-4372-8918-04387d31fe71",
      "terminada_en": "2026-10-17T13:14:41.037405Z",
      "tipo": "RUTEO_V2",
      "version_motor": "ruteo/v2",
      "version_politica": "ruteo-public-v2",
      "zona_horaria": "America/Mexico_City"
    }
  ],
  "problemas": [],
  "version": "app/v1"
};

export const validadoIncompatible: Respuesta<"/app/v1/contextos/validar"> = {
  "aviso": "app/v1 es de solo lectura: compone lo que cada motor ya publico, bajo un contexto explicito (el corte y cada ejecucion, por su identificador publico). No ejecuta, no envia, no llama, no visita, no optimiza ni reentrena nada, y nada se elige como 'lo ultimo'.",
  "compatible": false,
  "corte": {
    "cartera_id": "CARTERA_PRINCIPAL",
    "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
    "creado_en": "2026-10-17T13:14:16.920183Z",
    "cuentas": 1000,
    "dataset_id": "3eb2363e-c122-4eb5-abd3-ac9678aa43e8",
    "despacho_id": "DSP_001",
    "fecha_corte": "2026-09-30",
    "firma_contenido": "a5c54444360efe7c1619339ec5cf6ceff19c1917ed38bccf39b042a587f2def5",
    "run_id": "50b764f7-ddd2-459d-8bd4-8756df8c32b6",
    "version_modelo": "historia/v1"
  },
  "declaradas": {
    "ANALYTICS": "f2c5b154-a3f3-4105-94a0-f14032d00e37",
    "CAMPO_V2": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
    "DECISION_V2": "13c166a9-218f-435d-a36c-5aea9eaee03e",
    "OPTIMIZACION": "bb5fda47-20a1-4bf9-a466-69102e76b965",
    "RUTEO_V2": "4730febb-aa0c-4372-8918-04387d31fe71",
    "SCORING": "00000000-0000-4000-8000-00000000dead"
  },
  "derivadas": {
    "CONFIGURACION_CAMPO": "4cece266-3c7a-4ff1-bc62-d2a64989e9ac",
    "CONFIGURACION_OPTIMIZACION": "ec6c6274-a2a0-4999-b2f9-5721c78c5bda",
    "EVALUACION_PROMESAS": "d40af884-dfcd-461c-9efb-b2d1cef1930d",
    "GEOGRAFIA": "7455fff1-9fd1-4b8b-a217-cbfd1865f6e4",
    "RED_VIAL": "84d173b2-0b93-4838-97ac-2af88b18e5fc",
    "TERRITORIAL_V2": "d89b3565-7416-47a0-b4e7-e5bf22bc3e0b"
  },
  "ejecuciones": [
    {
      "as_of": "2026-09-30",
      "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "estado": "EXITOSA",
      "fecha_corte": "2026-09-30",
      "fecha_operacion": null,
      "firma_inputs": "b30987e15bb8cec05f12bc4019393cd5386e53224566dd7fa8c24fa908e8036d",
      "hash_politica": "2f9687400863f6af49a3c0fc51fe10c6c3b6f080804fcd4ef643ddad641d29e3",
      "iniciada_en": "2026-10-17T13:14:29.506615Z",
      "known_at": "2026-10-17T13:14:29.444442Z",
      "lee": {},
      "resultado": "DECISION_PUBLICADA",
      "run_id": "13c166a9-218f-435d-a36c-5aea9eaee03e",
      "terminada_en": "2026-10-17T13:14:30.301800Z",
      "tipo": "DECISION_V2",
      "version_motor": "decision/v2",
      "version_politica": "decision-policy/public-v1",
      "zona_horaria": "America/Mexico_City"
    },
    {
      "as_of": "2026-10-01",
      "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "estado": "EXITOSA",
      "fecha_corte": "2026-09-30",
      "fecha_operacion": null,
      "firma_inputs": "394a082a72e220a33a0e0da2300d4358e90d37d071e8f487573a8db73b5fa12a",
      "hash_politica": "3529b0893889bf8b28491c7fd2671cd3961b83915ba83084146585351fcb8a80",
      "iniciada_en": "2026-10-17T13:15:41.444118Z",
      "known_at": "2026-10-17T13:15:35.619443Z",
      "lee": {
        "DECISION_V2": "13c166a9-218f-435d-a36c-5aea9eaee03e",
        "EVALUACION_PROMESAS": "d40af884-dfcd-461c-9efb-b2d1cef1930d",
        "GEOGRAFIA": "7455fff1-9fd1-4b8b-a217-cbfd1865f6e4"
      },
      "resultado": "ANALYTICS_PUBLICADA",
      "run_id": "f2c5b154-a3f3-4105-94a0-f14032d00e37",
      "terminada_en": "2026-10-17T13:15:42.502268Z",
      "tipo": "ANALYTICS",
      "version_motor": "analytics/v1",
      "version_politica": "analytics-public-v1",
      "zona_horaria": "America/Mexico_City"
    },
    {
      "as_of": null,
      "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "estado": "EXITOSA",
      "fecha_corte": "2026-09-30",
      "fecha_operacion": "2026-10-01",
      "firma_inputs": "1526f329dfdd26da67b3ec4c59e3857985e2a1574774f1d03d2a2ec0f676fd77",
      "hash_politica": "540bc21fadd8297c2a8d427e8af540610642016e7dea5e9be87f997c53d23fc5",
      "iniciada_en": "2026-10-17T13:16:44.547155Z",
      "known_at": null,
      "lee": {
        "CAMPO_V2": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
        "CONFIGURACION_OPTIMIZACION": "ec6c6274-a2a0-4999-b2f9-5721c78c5bda",
        "DECISION_V2": "13c166a9-218f-435d-a36c-5aea9eaee03e",
        "RUTEO_V2": "4730febb-aa0c-4372-8918-04387d31fe71",
        "SCORING": "c1397868-406c-431b-ae0a-25a791e574ae"
      },
      "resultado": "PLAN_PUBLICADO",
      "run_id": "bb5fda47-20a1-4bf9-a466-69102e76b965",
      "terminada_en": "2026-10-17T13:16:45.184838Z",
      "tipo": "OPTIMIZACION",
      "version_motor": "optimization/v1",
      "version_politica": "optimization-public-v1",
      "zona_horaria": null
    },
    {
      "as_of": null,
      "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "estado": "EXITOSA",
      "fecha_corte": "2026-09-30",
      "fecha_operacion": "2026-10-01",
      "firma_inputs": "52fdbb54ae8f48a9733e33f1b83813495a8b91b45be19ed89301422da1bc81b7",
      "hash_politica": "b8d51c1441cc93a7b0d22416ac258bb134c70608bfef5ab1e37a6d7c59ecd854",
      "iniciada_en": "2026-10-17T13:14:39.341688Z",
      "known_at": null,
      "lee": {
        "CONFIGURACION_CAMPO": "4cece266-3c7a-4ff1-bc62-d2a64989e9ac",
        "DECISION_V2": "13c166a9-218f-435d-a36c-5aea9eaee03e",
        "GEOGRAFIA": "7455fff1-9fd1-4b8b-a217-cbfd1865f6e4",
        "TERRITORIAL_V2": "d89b3565-7416-47a0-b4e7-e5bf22bc3e0b"
      },
      "resultado": "PLAN_PUBLICADO",
      "run_id": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
      "terminada_en": "2026-10-17T13:14:39.873764Z",
      "tipo": "CAMPO_V2",
      "version_motor": "campo/v2",
      "version_politica": "campo-public-v2",
      "zona_horaria": "America/Mexico_City"
    },
    {
      "as_of": null,
      "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "estado": "EXITOSA",
      "fecha_corte": "2026-09-30",
      "fecha_operacion": "2026-10-01",
      "firma_inputs": "7362dd512045817fd08491b126e8a94a2193bb08b229f531f30e71219dcfdb09",
      "hash_politica": "6afe7b063d0151adfbac89efe6c49979a9507dc7257175e3fa514c3ebf31b097",
      "iniciada_en": "2026-10-17T13:14:40.497369Z",
      "known_at": null,
      "lee": {
        "CAMPO_V2": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
        "RED_VIAL": "84d173b2-0b93-4838-97ac-2af88b18e5fc"
      },
      "resultado": "RUTAS_PUBLICADAS",
      "run_id": "4730febb-aa0c-4372-8918-04387d31fe71",
      "terminada_en": "2026-10-17T13:14:41.037405Z",
      "tipo": "RUTEO_V2",
      "version_motor": "ruteo/v2",
      "version_politica": "ruteo-public-v2",
      "zona_horaria": "America/Mexico_City"
    }
  ],
  "problemas": [
    {
      "codigo": "EJECUCION_NO_ENCONTRADA",
      "ejecuciones": [
        "SCORING"
      ],
      "mensaje": "No existe el scoring 00000000-0000-4000-8000-00000000dead."
    },
    {
      "codigo": "ENCADENAMIENTO_DISTINTO",
      "ejecuciones": [
        "OPTIMIZACION",
        "SCORING"
      ],
      "mensaje": "La optimizacion bb5fda47-20a1-4bf9-a466-69102e76b965 lee el scoring c1397868-406c-431b-ae0a-25a791e574ae; el contexto declara el scoring 00000000-0000-4000-8000-00000000dead."
    }
  ],
  "version": "app/v1"
};

export const resumen: Respuesta<"/app/v1/resumen"> = {
  "analytics": {
    "as_of": "2026-10-01",
    "cuentas_cohorte": 1000,
    "embudos": [
      {
        "embudo": "CONTACTO",
        "etapas": [
          {
            "cuentas": 1000,
            "estado_valor": "DISPONIBLE",
            "etapa": "COHORTE",
            "metrica": "cartera.cuentas",
            "pct_cohorte": "1.0000000000",
            "pct_etapa_anterior": null
          },
          {
            "cuentas": 2,
            "estado_valor": "DISPONIBLE",
            "etapa": "GESTIONADAS",
            "metrica": "gestion.cuentas_gestionadas",
            "pct_cohorte": "0.0020000000",
            "pct_etapa_anterior": "0.0020000000"
          },
          {
            "cuentas": 1,
            "estado_valor": "DISPONIBLE",
            "etapa": "CONTACTADAS",
            "metrica": "gestion.cuentas_contactadas",
            "pct_cohorte": "0.0010000000",
            "pct_etapa_anterior": "0.5000000000"
          },
          {
            "cuentas": 1,
            "estado_valor": "DISPONIBLE",
            "etapa": "CON_PROMESA",
            "metrica": "promesa.cuentas_con_promesa",
            "pct_cohorte": "0.0010000000",
            "pct_etapa_anterior": "1.0000000000"
          }
        ]
      },
      {
        "embudo": "CAMPO",
        "etapas": [
          {
            "cuentas": null,
            "estado_valor": "NO_DISPONIBLE",
            "etapa": "CANDIDATAS",
            "metrica": "campo.candidatas",
            "pct_cohorte": null,
            "pct_etapa_anterior": null
          },
          {
            "cuentas": null,
            "estado_valor": "NO_DISPONIBLE",
            "etapa": "ASIGNADAS",
            "metrica": "campo.asignadas",
            "pct_cohorte": null,
            "pct_etapa_anterior": null
          },
          {
            "cuentas": null,
            "estado_valor": "NO_DISPONIBLE",
            "etapa": "PARADAS_PROGRAMADAS",
            "metrica": "ruteo.programadas",
            "pct_cohorte": null,
            "pct_etapa_anterior": null
          }
        ]
      }
    ],
    "estado": "EXITOSA",
    "estado_fuente": "DISPONIBLE",
    "granularidad": "DIA",
    "hash_catalogo": "553326f78c2ab17b48c8768af5e6dbb2c039f4b452aad092eb42b4113eacff64",
    "indicadores": [
      {
        "denominador": null,
        "estado_valor": "DISPONIBLE",
        "metrica": "cartera.cuentas",
        "motivos": [],
        "nombre": "cuentas",
        "numerador": null,
        "valor": "1000"
      },
      {
        "denominador": null,
        "estado_valor": "DISPONIBLE",
        "metrica": "cartera.saldo_total",
        "motivos": [],
        "nombre": "saldo_total",
        "numerador": null,
        "valor": "37691314.06"
      },
      {
        "denominador": null,
        "estado_valor": "NO_DISPONIBLE",
        "metrica": "recuperacion.neta",
        "motivos": [
          {
            "codigo": "PAGOS_SIN_COBERTURA",
            "desde": "2026-10-01",
            "dias": 1,
            "hasta": "2026-10-01"
          }
        ],
        "nombre": "recuperacion_neta",
        "numerador": null,
        "valor": null
      },
      {
        "denominador": null,
        "estado_valor": "NO_DISPONIBLE",
        "metrica": "recuperacion.sobre_saldo_inicial",
        "motivos": [
          {
            "codigo": "PAGOS_SIN_COBERTURA",
            "desde": "2026-10-01",
            "dias": 1,
            "hasta": "2026-10-01"
          }
        ],
        "nombre": "recuperacion_sobre_saldo_inicial",
        "numerador": null,
        "valor": null
      },
      {
        "denominador": null,
        "estado_valor": "DISPONIBLE",
        "metrica": "gestion.cuentas_gestionadas",
        "motivos": [],
        "nombre": "cuentas_gestionadas",
        "numerador": null,
        "valor": "2"
      },
      {
        "denominador": "2",
        "estado_valor": "DISPONIBLE",
        "metrica": "gestion.contactadas_sobre_gestionadas",
        "motivos": [],
        "nombre": "contactabilidad",
        "numerador": "1",
        "valor": "0.5000000000"
      },
      {
        "denominador": null,
        "estado_valor": "DISPONIBLE",
        "metrica": "promesa.creadas",
        "motivos": [],
        "nombre": "promesas_creadas",
        "numerador": null,
        "valor": "1"
      },
      {
        "denominador": "0",
        "estado_valor": "DISPONIBLE",
        "metrica": "promesa.tasa_cumplimiento_total",
        "motivos": [
          {
            "codigo": "DENOMINADOR_CERO"
          }
        ],
        "nombre": "tasa_cumplimiento_promesas",
        "numerador": "0",
        "valor": null
      },
      {
        "denominador": null,
        "estado_valor": "NO_DISPONIBLE",
        "metrica": "atribucion.asociacion_unica",
        "motivos": [
          {
            "codigo": "PAGOS_SIN_COBERTURA",
            "desde": "2026-10-01",
            "dias": 1,
            "hasta": "2026-10-01"
          }
        ],
        "nombre": "asociacion_unica",
        "numerador": null,
        "valor": null
      },
      {
        "denominador": null,
        "estado_valor": "NO_DISPONIBLE",
        "metrica": "campo.candidatas",
        "motivos": [
          {
            "codigo": "ENTRADA_NO_DECLARADA",
            "entradas": [
              "CAMPO_V2",
              "TERRITORIAL_V2"
            ]
          }
        ],
        "nombre": "campo_candidatas",
        "numerador": null,
        "valor": null
      },
      {
        "denominador": null,
        "estado_valor": "NO_DISPONIBLE",
        "metrica": "campo.asignadas",
        "motivos": [
          {
            "codigo": "ENTRADA_NO_DECLARADA",
            "entradas": [
              "CAMPO_V2",
              "TERRITORIAL_V2"
            ]
          }
        ],
        "nombre": "campo_asignadas",
        "numerador": null,
        "valor": null
      },
      {
        "denominador": null,
        "estado_valor": "NO_DISPONIBLE",
        "metrica": "ruteo.paradas_objetivo",
        "motivos": [
          {
            "codigo": "ENTRADA_NO_DECLARADA",
            "entradas": [
              "CAMPO_V2",
              "RUTEO_V2",
              "TERRITORIAL_V2"
            ]
          }
        ],
        "nombre": "ruteo_paradas_objetivo",
        "numerador": null,
        "valor": null
      },
      {
        "denominador": null,
        "estado_valor": "NO_DISPONIBLE",
        "metrica": "ruteo.programadas",
        "motivos": [
          {
            "codigo": "ENTRADA_NO_DECLARADA",
            "entradas": [
              "CAMPO_V2",
              "RUTEO_V2",
              "TERRITORIAL_V2"
            ]
          }
        ],
        "nombre": "ruteo_programadas",
        "numerador": null,
        "valor": null
      }
    ],
    "known_at": "2026-10-17T13:15:35.619443Z",
    "resultado": "ANALYTICS_PUBLICADA",
    "run_id": "f2c5b154-a3f3-4105-94a0-f14032d00e37",
    "version_catalogo": "analytics-metrics/public-v1",
    "zona_horaria": "America/Mexico_City"
  },
  "avisos": [
    "app/v1 es de solo lectura: compone lo que cada motor ya publico, bajo un contexto explicito (el corte y cada ejecucion, por su identificador publico). No ejecuta, no envia, no llama, no visita, no optimiza ni reentrena nada, y nada se elige como 'lo ultimo'.",
    "Prediccion, no efecto causal ni recomendacion de accion.",
    "Optimo respecto al modelo declarado; no estrategia causalmente optima.",
    "Asociacion operacional; no implica causalidad."
  ],
  "campo": {
    "capacidad": {
      "capacidad_total": 49,
      "capacidad_utilizada": 24,
      "utilizacion": "0.4898"
    },
    "conteos": {
      "asignadas": 24,
      "candidatas_campo": 153,
      "cuentas_evaluadas_decision": 1000,
      "rebalanceadas": 11,
      "revision_territorial": 129,
      "sin_capacidad": 0,
      "sin_gestor": 0
    },
    "estado": "EXITOSA",
    "estado_fuente": "DISPONIBLE",
    "fecha_operacion": "2026-10-01",
    "resultado": "PLAN_PUBLICADO",
    "run_id": "581de30e-9e76-4b3a-a722-bf0289e1b25b"
  },
  "corte": {
    "cartera_id": "CARTERA_PRINCIPAL",
    "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
    "creado_en": "2026-10-17T13:14:16.920183Z",
    "cuentas": 1000,
    "dataset_id": "3eb2363e-c122-4eb5-abd3-ac9678aa43e8",
    "despacho_id": "DSP_001",
    "fecha_corte": "2026-09-30",
    "firma_contenido": "a5c54444360efe7c1619339ec5cf6ceff19c1917ed38bccf39b042a587f2def5",
    "run_id": "50b764f7-ddd2-459d-8bd4-8756df8c32b6",
    "version_modelo": "historia/v1"
  },
  "decision": {
    "as_of": "2026-09-30",
    "cuentas_decididas": 1000,
    "distribucion": {
      "ajuste_total": {
        "-1": 357,
        "0": 642,
        "1": 1
      },
      "canal_recomendado": {
        "CAMPO": 153,
        "DIGITAL": 546,
        "TELEFONICA": 301
      },
      "cuentas": 1000,
      "modificadores": {
        "PROMESA_INCUMPLIDA_RECIENTE": 0,
        "PROMESA_VIGENTE": 0,
        "RECUPERACION_RECIENTE": 358,
        "SATURACION_SIN_CONTACTO": 2
      },
      "prioridad_final": {
        "ALTA": 301,
        "BAJA": 208,
        "MEDIA": 338,
        "MUY_ALTA": 153
      }
    },
    "estado": "EXITOSA",
    "estado_fuente": "DISPONIBLE",
    "hash_politica": "2f9687400863f6af49a3c0fc51fe10c6c3b6f080804fcd4ef643ddad641d29e3",
    "known_at": "2026-10-17T13:14:29.444442Z",
    "resultado": "DECISION_PUBLICADA",
    "run_id": "13c166a9-218f-435d-a36c-5aea9eaee03e",
    "version_politica": "decision-policy/public-v1"
  },
  "optimizacion": {
    "best_bound": "30197437.0000",
    "cuentas_asignadas": 39,
    "cuentas_elegibles": 1000,
    "cuentas_universo": 1000,
    "estado": "EXITOSA",
    "estado_fuente": "DISPONIBLE",
    "fecha_operacion": "2026-10-01",
    "mip_gap": "0E-12",
    "objetivo_primario_centavos": 30197437,
    "por_canal": {
      "CAMPO": {
        "capacidad": 9,
        "pools": 3,
        "unidades": 180,
        "utilizacion": 1.0,
        "utilizada": 9,
        "valor_centavos": 286879
      },
      "DIGITAL": {
        "capacidad": 20,
        "pools": 1,
        "unidades": 20,
        "utilizacion": 1.0,
        "utilizada": 20,
        "valor_centavos": 20302238
      },
      "TELEFONICA": {
        "capacidad": 10,
        "pools": 1,
        "unidades": 40,
        "utilizacion": 1.0,
        "utilizada": 10,
        "valor_centavos": 9608320
      }
    },
    "resultado": "PLAN_PUBLICADO",
    "run_id": "bb5fda47-20a1-4bf9-a466-69102e76b965",
    "solver_seconds": "0.011",
    "solver_status": "OPTIMAL"
  },
  "predictivo": {
    "as_of": "2026-09-30",
    "distribucion": {
      "aviso": "Prediccion, no efecto causal ni recomendacion de accion.",
      "bins": 10,
      "con_imputacion": 1000,
      "histogramas": [
        {
          "cuentas": [
            3,
            120,
            126,
            118,
            155,
            329,
            140,
            9,
            0,
            0
          ],
          "salida": "p_recuperacion_positiva_30d"
        },
        {
          "cuentas": [
            3,
            152,
            392,
            443,
            10,
            0,
            0,
            0,
            0,
            0
          ],
          "salida": "p_contacto_14d"
        },
        {
          "cuentas": [
            0,
            328,
            672,
            0,
            0,
            0,
            0,
            0,
            0,
            0
          ],
          "salida": "p_promesa_30d"
        }
      ],
      "medias": {
        "p_contacto_14d": "0.27139607",
        "p_promesa_30d": "0.20593123",
        "p_recuperacion_positiva_30d": "0.44005066"
      },
      "modelo_version_id": "261a485b-7f86-44f9-808a-190f164fd471",
      "publicadas": 1000,
      "scoring_run_id": "c1397868-406c-431b-ae0a-25a791e574ae",
      "suma_recuperacion_positiva_esperada_30d": "1118947.24"
    },
    "drift_resumen": {
      "aviso": "El drift describe cuanto se parece lo puntuado a TRAIN. No cambia ni reentrena el modelo; sus umbrales son heuristicas operacionales.",
      "cartera_distinta": true,
      "faltantes": {
        "ALTO": 6,
        "BAJO": 28,
        "MODERADO": 3
      },
      "feature": {
        "ALTO": 16,
        "BAJO": 22,
        "MODERADO": 2
      },
      "mayor_psi": [
        {
          "nombre": "delta_dias_atraso_1_corte",
          "psi": 8.823712,
          "severidad": "ALTO",
          "tipo": "FEATURE"
        },
        {
          "nombre": "tendencia_atraso",
          "psi": 8.567815,
          "severidad": "ALTO",
          "tipo": "FEATURE"
        },
        {
          "nombre": "dias_desde_ultimo_contacto",
          "psi": 8.525797,
          "severidad": "ALTO",
          "tipo": "FEATURE"
        },
        {
          "nombre": "delta_dias_atraso_4_semanas",
          "psi": 8.254702,
          "severidad": "ALTO",
          "tipo": "FEATURE"
        },
        {
          "nombre": "dias_desde_ultima_promesa",
          "psi": 7.971063,
          "severidad": "ALTO",
          "tipo": "FEATURE"
        }
      ],
      "score": {
        "ALTO": 2,
        "BAJO": 1,
        "MODERADO": 1
      }
    },
    "estado": "EXITOSA",
    "estado_fuente": "DISPONIBLE",
    "known_at": "2026-10-17T13:14:29.444442Z",
    "modelo_version_id": "261a485b-7f86-44f9-808a-190f164fd471",
    "resultado": "PREDICCIONES_PUBLICADAS",
    "run_id": "c1397868-406c-431b-ae0a-25a791e574ae",
    "version_modelo": "predictive/model-v1"
  },
  "ruteo": {
    "conteos": {
      "bases_no_enrutables": 0,
      "gestores_objetivo": 3,
      "no_enrutables": 3,
      "no_enrutables_base": 0,
      "paradas_objetivo": 24,
      "programadas": 9,
      "rutas_completas": 1,
      "rutas_parciales": 2,
      "rutas_sin_jornada": 0,
      "rutas_sin_paradas": 0,
      "sin_tiempo": 12
    },
    "estado": "EXITOSA",
    "estado_fuente": "DISPONIBLE",
    "fecha_operacion": "2026-10-01",
    "resultado": "RUTAS_PUBLICADAS",
    "run_id": "4730febb-aa0c-4372-8918-04387d31fe71",
    "totales": {
      "distancia_total_m": 10598,
      "duracion_espera_total_s": 4774,
      "duracion_servicio_total_s": 8100,
      "duracion_vial_total_s": 1544
    }
  }
};

export const cuentas: Respuesta<"/app/v1/cuentas"> = {
  "aviso": "app/v1 es de solo lectura: compone lo que cada motor ya publico, bajo un contexto explicito (el corte y cada ejecucion, por su identificador publico). No ejecuta, no envia, no llama, no visita, no optimiza ni reentrena nada, y nada se elige como 'lo ultimo'.",
  "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
  "elementos": [
    {
      "atraso_bucket": "31-60",
      "canal_asignado": null,
      "canal_recomendado": "CAMPO",
      "cuenta_id": "261eac0b-af0b-5572-8450-0dfc9d268d07",
      "dias_atraso": 43,
      "estado_optimizacion": "NO_SELECCIONADA",
      "estado_prediccion": "CON_IMPUTACION",
      "p_recuperacion_positiva_30d": "0.47956791",
      "prioridad": "MUY_ALTA",
      "producto": "CONSUMO",
      "recuperacion_positiva_esperada_30d": "2821.95",
      "saldo_total": "48328.28",
      "valor_oportunidad_centavos": 282195
    },
    {
      "atraso_bucket": "31-60",
      "canal_asignado": null,
      "canal_recomendado": "TELEFONICA",
      "cuenta_id": "8063b735-17f3-50d5-b006-9a1bba514e97",
      "dias_atraso": 54,
      "estado_optimizacion": "NO_SELECCIONADA",
      "estado_prediccion": "CON_IMPUTACION",
      "p_recuperacion_positiva_30d": "0.53124705",
      "prioridad": "ALTA",
      "producto": "TARJETA",
      "recuperacion_positiva_esperada_30d": "1854.45",
      "saldo_total": "44525.22",
      "valor_oportunidad_centavos": 185445
    },
    {
      "atraso_bucket": "1-30",
      "canal_asignado": null,
      "canal_recomendado": "DIGITAL",
      "cuenta_id": "6a566778-9a59-59cd-9d0f-4a89be4c383b",
      "dias_atraso": 4,
      "estado_optimizacion": "NO_SELECCIONADA",
      "estado_prediccion": "CON_IMPUTACION",
      "p_recuperacion_positiva_30d": "0.53421614",
      "prioridad": "MEDIA",
      "producto": "CONSUMO",
      "recuperacion_positiva_esperada_30d": "845.20",
      "saldo_total": "21317.33",
      "valor_oportunidad_centavos": 84520
    },
    {
      "atraso_bucket": "91+",
      "canal_asignado": null,
      "canal_recomendado": "CAMPO",
      "cuenta_id": "740638fb-356f-5b51-a342-7e6adc0227da",
      "dias_atraso": 93,
      "estado_optimizacion": "NO_SELECCIONADA",
      "estado_prediccion": "CON_IMPUTACION",
      "p_recuperacion_positiva_30d": "0.25226050",
      "prioridad": "MUY_ALTA",
      "producto": "TARJETA",
      "recuperacion_positiva_esperada_30d": "280.30",
      "saldo_total": "17153.85",
      "valor_oportunidad_centavos": 28030
    },
    {
      "atraso_bucket": "0",
      "canal_asignado": null,
      "canal_recomendado": "DIGITAL",
      "cuenta_id": "02c6dfad-c9cb-51cb-bf56-d32f0fa7a69b",
      "dias_atraso": 0,
      "estado_optimizacion": "NO_SELECCIONADA",
      "estado_prediccion": "CON_IMPUTACION",
      "p_recuperacion_positiva_30d": "0.62723841",
      "prioridad": "BAJA",
      "producto": "CONSUMO",
      "recuperacion_positiva_esperada_30d": "1033.04",
      "saldo_total": "27452.64",
      "valor_oportunidad_centavos": 103304
    }
  ],
  "orden": "CUENTA",
  "pagina": 1,
  "por_pagina": 5,
  "sentido": "ASC",
  "total": 1000
};

export const cuenta: Respuesta<"/app/v1/cuentas/{cuenta_id}"> = {
  "avisos": [
    "app/v1 es de solo lectura: compone lo que cada motor ya publico, bajo un contexto explicito (el corte y cada ejecucion, por su identificador publico). No ejecuta, no envia, no llama, no visita, no optimiza ni reentrena nada, y nada se elige como 'lo ultimo'.",
    "Prediccion, no efecto causal ni recomendacion de accion.",
    "Optimo respecto al modelo declarado; no estrategia causalmente optima.",
    "Asociacion operacional; no implica causalidad."
  ],
  "campo": {
    "asignacion": {
      "base": {
        "base_id": "faa1f6d4-ad78-45c9-b56a-028054f2c675",
        "codigo": "BASE_RUTEO"
      },
      "campo_run_id": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
      "configuracion_campo_version_id": "4cece266-3c7a-4ff1-bc62-d2a64989e9ac",
      "corte": {
        "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
        "fecha_corte": "2026-09-30"
      },
      "cuenta_id": "41095c46-babd-53a1-a59b-231da3e9d99a",
      "es_rebalanceada": true,
      "estado": "ASIGNADA",
      "fecha_operacion": "2026-10-01",
      "gestor": {
        "codigo": "GESTOR_RUTA_002",
        "gestor_id": "2fb917e5-02ff-4be4-8b91-8b9e0a1596e0"
      },
      "microtramo_id": "8fa36f85-4e0b-5735-9a2e-4eecca27b02e",
      "motivos": [
        {
          "codigo": "SPILLOVER_POR_CAPACIDAD",
          "cuentas_bloque": 2,
          "dividido": false,
          "gestor": "GESTOR_RUTA_002",
          "lugar": 2,
          "propietario_preferido": "GESTOR_RUTA_001"
        }
      ],
      "municipio": "21114",
      "orden_prioridad": 11,
      "prioridad": "MUY_ALTA",
      "subzona_id": "c6d77e81-f49c-5091-82e1-08fa3b34a7dd",
      "tramo_id": "bbc2d9e1-8c60-5fcd-b8d1-792beed93273",
      "version_politica": "campo-public-v2",
      "zona_id": "8053ddd6-dbe4-5d06-a802-27ed80de2a72"
    },
    "carga_gestor": {
      "asignadas": 6,
      "base": {
        "base_id": "faa1f6d4-ad78-45c9-b56a-028054f2c675",
        "codigo": "BASE_RUTEO"
      },
      "capacidad": 6,
      "estado_jornada": "JORNADA_SEMANAL",
      "gestor": {
        "codigo": "GESTOR_RUTA_002",
        "gestor_id": "2fb917e5-02ff-4be4-8b91-8b9e0a1596e0"
      },
      "microtramos": 3,
      "rebalanceadas": 2,
      "tramos": 1,
      "utilizacion": "1.0000",
      "ventanas": [
        {
          "fin": "09:45",
          "fin_en": "2026-10-01T15:45:00Z",
          "inicio": "09:00",
          "inicio_en": "2026-10-01T15:00:00Z"
        },
        {
          "fin": "14:00",
          "fin_en": "2026-10-01T20:00:00Z",
          "inicio": "11:00",
          "inicio_en": "2026-10-01T17:00:00Z"
        }
      ]
    },
    "estado": "DISPONIBLE",
    "run_id": "581de30e-9e76-4b3a-a722-bf0289e1b25b"
  },
  "cartera_id": "CARTERA_PRINCIPAL",
  "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
  "cuenta_id": "41095c46-babd-53a1-a59b-231da3e9d99a",
  "decision": {
    "ajuste_total": 0,
    "as_of": "2026-09-30",
    "canal_recomendado": "CAMPO",
    "decision_hash": "5f9fb0f73882b3d12223c456e7940c4486d428b0d0995b334373c062874c1f3e",
    "estado": "DISPONIBLE",
    "known_at": "2026-10-17T13:14:29.444442Z",
    "motivos": [
      "PRIORIDAD_BASE_MORA",
      "PRIORIDAD_FINAL",
      "CANAL_POR_PRIORIDAD"
    ],
    "prioridad_base": "MUY_ALTA",
    "prioridad_final": "MUY_ALTA",
    "run_id": "13c166a9-218f-435d-a36c-5aea9eaee03e"
  },
  "despacho_id": "DSP_001",
  "en_el_corte": true,
  "fecha_corte": "2026-09-30",
  "geografia": {
    "codigo_base": "BASE_RUTEO",
    "comparacion_municipio": "COINCIDE",
    "cvegeo": "21114",
    "distancia_base_m": "1020.99",
    "estado": "DISPONIBLE",
    "estado_asignacion": "ASIGNADA",
    "estado_geografia": "VALIDA",
    "geografia_run_id": "7455fff1-9fd1-4b8b-a217-cbfd1865f6e4",
    "municipio": "Puebla (sintetico)",
    "municipio_fuente": "21114",
    "nombre_base": "Base de ruteo (sintetica, en un cruce de la red)",
    "run_id": "d89b3565-7416-47a0-b4e7-e5bf22bc3e0b",
    "territorial_run_id": "d89b3565-7416-47a0-b4e7-e5bf22bc3e0b"
  },
  "optimizacion": {
    "aviso": "Optimo respecto al modelo declarado; no estrategia causalmente optima.",
    "estado": "DISPONIBLE",
    "fecha_operacion": "2026-10-01",
    "limitaciones": [
      "Es el optimo del modelo de asignacion declarado, no una estrategia causalmente optima.",
      "El valor es la recuperacion positiva esperada bajo la politica historica, no el beneficio incremental de contactar.",
      "Un canal asignado es una asignacion de capacidad compatible con las restricciones, no la evidencia de que ese canal cause mayor recuperacion.",
      "Las capacidades de un benchmark son sinteticas, y las unidades de recurso no son costos reales.",
      "Un optimo matematico de supuestos incorrectos sigue siendo una mala decision de negocio.",
      "Es un plan de soporte a decision, en sombra: no registra gestiones, no envia mensajes, no llama, no crea visitas ni cambia decision/v2, campo/v2 o ruteo/v2."
    ],
    "resultado": {
      "atraso_bucket": "91+",
      "base": "BASE_RUTEO",
      "canal_asignado": "CAMPO",
      "canal_decision": "CAMPO",
      "cuenta_id": "41095c46-babd-53a1-a59b-231da3e9d99a",
      "estado": "ASIGNADA",
      "gestor": "GESTOR_RUTA_002",
      "motivo": "EN_SOLUCION_OPTIMA",
      "orden_ruteo_original": 1,
      "p_contacto_14d": "0.29328641",
      "pool": "CAMPO_GESTOR_RUTA_002",
      "prioridad": "MUY_ALTA",
      "producto": "CONSUMO",
      "retenida": true,
      "unidades_recurso": 20,
      "valor_oportunidad_centavos": 24162
    },
    "run_id": "bb5fda47-20a1-4bf9-a466-69102e76b965"
  },
  "predictivo": {
    "aviso": "Prediccion, no efecto causal ni recomendacion de accion.",
    "estado": "DISPONIBLE",
    "prediccion": {
      "as_of": "2026-09-30",
      "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "estado": "CON_IMPUTACION",
      "feature_hash": "aad459d9bac5fdd12c59cd51702b7ebbeae38ef5c35f4a702e6b2693d0ffd7f3",
      "known_at": "2026-10-17T13:14:29.444442Z",
      "modelo_version_id": "261a485b-7f86-44f9-808a-190f164fd471",
      "motivos": [
        {
          "features": [
            "recuperacion_neta_30d_pasados",
            "recuperacion_neta_90d_pasados",
            "movimientos_30d",
            "movimientos_90d",
            "pagos_positivos_30d",
            "pagos_positivos_90d",
            "reversos_90d",
            "dias_desde_ultimo_movimiento",
            "dias_desde_ultima_recuperacion_positiva"
          ],
          "motivo": "SIN_COBERTURA_PAGOS"
        },
        {
          "features": [
            "delta_saldo_1_corte",
            "delta_saldo_4_semanas",
            "delta_dias_atraso_1_corte",
            "delta_dias_atraso_4_semanas",
            "tendencia_saldo",
            "tendencia_atraso"
          ],
          "motivo": "SIN_HISTORIA"
        }
      ],
      "p_contacto_14d": "0.29328641",
      "p_promesa_30d": "0.23634787",
      "p_recuperacion_positiva_30d": "0.26167243",
      "recuperacion_positiva_esperada_30d": "241.62",
      "riesgo_sin_recuperacion_30d": "0.73832757",
      "scoring_run_id": "c1397868-406c-431b-ae0a-25a791e574ae"
    },
    "run_id": "c1397868-406c-431b-ae0a-25a791e574ae"
  },
  "presencia": {
    "cortes_ausentes_desde_primera_observacion": 0,
    "cortes_observados": 1,
    "estado_presencia": "EN_CARTERA",
    "pagos_observados": 0,
    "primera_observacion": "2026-09-30",
    "reingresos_observados": 0,
    "salidas_observadas": 0,
    "ultima_observacion": "2026-09-30",
    "ultimo_corte_cartera": "2026-09-30"
  },
  "ruteo": {
    "estado": "DISPONIBLE",
    "parada": {
      "base": "BASE_RUTEO",
      "cuenta_id": "41095c46-babd-53a1-a59b-231da3e9d99a",
      "distancia_desde_anterior_m": 1398,
      "duracion_desde_anterior_s": 203,
      "espera_s": 0,
      "estado": "PROGRAMADA",
      "estado_ruta": "PARCIAL",
      "fecha_operacion": "2026-10-01",
      "fin_servicio": "2026-10-01T09:18:23-06:00",
      "gestor": "GESTOR_RUTA_002",
      "inicio_servicio": "2026-10-01T09:03:23-06:00",
      "llegada_estimada": "2026-10-01T09:03:23-06:00",
      "motivo": null,
      "orden_visita": 1,
      "prioridad": "MUY_ALTA",
      "ruta_id": "8399224e-90bc-5ee6-a282-8b58a249cecd",
      "ruteo_run_id": "4730febb-aa0c-4372-8918-04387d31fe71",
      "salida_anterior": "2026-10-01T09:00:00-06:00",
      "snap_distancia_m": "60.04",
      "version_politica": "ruteo-public-v2",
      "zona_horaria": "America/Mexico_City"
    },
    "run_id": "4730febb-aa0c-4372-8918-04387d31fe71"
  },
  "snapshot": {
    "atraso_maximo": 195,
    "canal": "TELEFONICA",
    "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
    "cve_entidad": "21",
    "cve_municipio": "114",
    "dataset_id": "3eb2363e-c122-4eb5-abd3-ac9678aa43e8",
    "dias_atraso": 141,
    "estatus_plan": null,
    "estatus_promesa_pago": null,
    "estrategia": "RECUPERACION",
    "fecha_corte": "2026-09-30",
    "fecha_ultimo_pago": "2026-06-06",
    "imp_ultimo_pago": "1352.10",
    "monto_plan": null,
    "monto_promesa_pago": null,
    "moratorios": "3145.66",
    "pago_normal": "286.02",
    "pagos_recibidos": null,
    "producto": "CONSUMO",
    "saldo": "14873.12",
    "saldo_atrasado": "6006.42",
    "saldo_requerido": "6006.42",
    "saldo_total": "18018.78",
    "semanas_atraso": 21,
    "source_row": 12,
    "source_sheet": "CARTERA.csv"
  }
};

export const decisionDeCuenta: Respuesta<"/app/v1/cuentas/{cuenta_id}/decision"> = {
  "ajuste_total": 0,
  "as_of": "2026-09-30",
  "aviso": "decision/v2 es un motor de reglas deterministas, point-in-time y explicables, sobre datos sinteticos: no es un modelo predictivo, y un nivel es el lugar de una prioridad en su escala, no una probabilidad. decision-policy/public-v1 demuestra la capacidad del motor; no representa una politica real de un acreedor. Corre en paralelo a decision/v1, que sigue alimentando el flujo operacional.",
  "canal_recomendado": "CAMPO",
  "cuenta_id": "41095c46-babd-53a1-a59b-231da3e9d99a",
  "decision_hash": "5f9fb0f73882b3d12223c456e7940c4486d428b0d0995b334373c062874c1f3e",
  "decision_run_id": "13c166a9-218f-435d-a36c-5aea9eaee03e",
  "feature_hash": "72bb19e096acfa3ba0fe914c0a08708299423408fe2f0bb7225ce0c4db86130a",
  "features": {
    "contactos_tercero_30d": 0,
    "contactos_titular_30d": 0,
    "continuo_desde_corte_anterior": null,
    "convenio_vigente": false,
    "convenios_90d": 0,
    "cortes_observados": 1,
    "delta_dias_atraso_desde_corte_anterior": null,
    "delta_saldo_total_desde_corte_anterior": null,
    "dias_atraso": 141,
    "dias_desde_ultima_gestion": null,
    "dias_desde_ultimo_pago": null,
    "dias_gestion_a_pago_mediana_90d": null,
    "estatus_plan": null,
    "estrategia": "RECUPERACION",
    "gestiones_30d": 0,
    "gestiones_7d": 0,
    "gestiones_contactables_30d": 0,
    "gestiones_sin_contacto_7d": 0,
    "moratorios": "3145.66",
    "movimientos_pago_30d": 0,
    "movimientos_pago_7d": 0,
    "pagos_asociados_unicos_30d": 0,
    "producto": "CONSUMO",
    "promesa_vigente": false,
    "promesas_30d": 0,
    "promesas_90d": 0,
    "promesas_cumplidas_90d": 0,
    "promesas_incumplidas_30d": 0,
    "promesas_incumplidas_90d": 0,
    "promesas_parciales_90d": 0,
    "recuperacion_asociada_unica_30d": "0.00",
    "recuperacion_interpretada_30d": "0.00",
    "recuperacion_interpretada_7d": "0.00",
    "recuperacion_interpretada_90d": null,
    "reingresos_observados": 0,
    "saldo_atrasado": "6006.42",
    "saldo_requerido": "6006.42",
    "saldo_total": "18018.78",
    "semanas_atraso": 21,
    "tasa_contacto_titular_30d": null,
    "ultima_gestion_canal": null,
    "ultima_gestion_nivel_contacto": null,
    "visitas_30d": 0
  },
  "hash_politica": "2f9687400863f6af49a3c0fc51fe10c6c3b6f080804fcd4ef643ddad641d29e3",
  "known_at": "2026-10-17T13:14:29.444442Z",
  "motivos": [
    {
      "codigo": "PRIORIDAD_BASE_MORA",
      "condiciones": [
        {
          "feature": "dias_atraso",
          "operador": "EN_TRAMO",
          "umbral": "91+",
          "valor": "141"
        }
      ],
      "nivel": 4,
      "prioridad": "MUY_ALTA",
      "segmento": "MORA_ALTA"
    },
    {
      "acotada": false,
      "ajuste_total": 0,
      "codigo": "PRIORIDAD_FINAL",
      "nivel": 4,
      "nivel_base": 4,
      "nivel_sin_acotar": 4,
      "prioridad": "MUY_ALTA"
    },
    {
      "canal": "CAMPO",
      "codigo": "CANAL_POR_PRIORIDAD",
      "prioridad": "MUY_ALTA"
    }
  ],
  "prioridad_base": "MUY_ALTA",
  "prioridad_final": "MUY_ALTA",
  "reglas": [
    {
      "activa": false,
      "codigo": "RECUPERACION_RECIENTE",
      "condiciones": [
        {
          "cumple": false,
          "feature": "recuperacion_interpretada_7d",
          "operador": ">",
          "umbral": "0.00",
          "valor": "0.00"
        }
      ],
      "efecto": 0
    },
    {
      "activa": false,
      "codigo": "PROMESA_VIGENTE",
      "condiciones": [
        {
          "cumple": false,
          "feature": "promesa_vigente",
          "operador": "=",
          "umbral": "true",
          "valor": "false"
        }
      ],
      "efecto": 0
    },
    {
      "activa": false,
      "codigo": "SATURACION_SIN_CONTACTO",
      "condiciones": [
        {
          "cumple": false,
          "feature": "gestiones_sin_contacto_7d",
          "operador": ">=",
          "umbral": "3",
          "valor": "0"
        },
        {
          "cumple": true,
          "feature": "contactos_titular_30d",
          "operador": "=",
          "umbral": "0",
          "valor": "0"
        }
      ],
      "efecto": 0
    },
    {
      "activa": false,
      "codigo": "PROMESA_INCUMPLIDA_RECIENTE",
      "condiciones": [
        {
          "cumple": false,
          "feature": "promesas_incumplidas_30d",
          "operador": ">=",
          "umbral": "1",
          "valor": "0"
        }
      ],
      "efecto": 0
    }
  ],
  "score_nivel_base": 4,
  "score_nivel_final": 4,
  "verificacion": {
    "decision_hash": true,
    "feature_hash": true,
    "motivos": true
  },
  "versiones": {
    "features": "features-decision/v1",
    "motor": "decision/v2",
    "politica": "decision-policy/public-v1"
  }
};

export const historiaDeCuenta: Respuesta<"/app/v1/cuentas/{cuenta_id}/historia"> = {
  "cuenta_id": "41095c46-babd-53a1-a59b-231da3e9d99a",
  "elementos": [
    {
      "atraso_maximo": 195,
      "canal": "TELEFONICA",
      "continuo_desde_anterior": null,
      "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "cortes_ausentes_desde_anterior": null,
      "cve_entidad": "21",
      "cve_municipio": "114",
      "dataset_id": "3eb2363e-c122-4eb5-abd3-ac9678aa43e8",
      "delta_dias_atraso": null,
      "delta_saldo_total": null,
      "dias_atraso": 141,
      "estatus_plan": null,
      "estatus_promesa_pago": null,
      "estrategia": "RECUPERACION",
      "fecha_corte": "2026-09-30",
      "fecha_ultimo_pago": "2026-06-06",
      "imp_ultimo_pago": "1352.10",
      "monto_plan": null,
      "monto_promesa_pago": null,
      "moratorios": "3145.66",
      "pago_normal": "286.02",
      "pagos_recibidos": null,
      "producto": "CONSUMO",
      "saldo": "14873.12",
      "saldo_atrasado": "6006.42",
      "saldo_requerido": "6006.42",
      "saldo_total": "18018.78",
      "semanas_atraso": 21,
      "source_row": 12,
      "source_sheet": "CARTERA.csv"
    }
  ],
  "orden": "desc",
  "pagina": 1,
  "por_pagina": 5,
  "total": 1
};

export const lineaDeTiempo: Respuesta<"/app/v1/cuentas/{cuenta_id}/linea-de-tiempo"> = {
  "aviso": "Eventos operacionales que registra Motor Cartera: lo que la cobranza hizo, con su momento de negocio (ocurrido_en) y su momento de registro (registrado_en). No son una fuente oficial del acreedor, y lo que un corte dice de una promesa o de un plan es una observacion de la fuente, no un evento.",
  "cuenta_id": "41095c46-babd-53a1-a59b-231da3e9d99a",
  "elementos": [],
  "orden": "desc",
  "pagina": 1,
  "por_pagina": 5,
  "total": 0,
  "version_motor": "motor-pagos/v1"
};

export const pagosDeCuenta: Respuesta<"/app/v1/cuentas/{cuenta_id}/pagos"> = {
  "aviso_atribucion": "Asociacion operacional; no implica causalidad.",
  "cuenta_id": "41095c46-babd-53a1-a59b-231da3e9d99a",
  "pagina": {
    "aviso": "Asociacion operacional, no causalidad: dice que gestiones con contacto de la misma cuenta ocurrieron antes de un pago, dentro de la ventana de la ejecucion, no que una gestion lo haya producido. Con dos o mas candidatas el pago queda AMBIGUA y no se elige ninguna.",
    "cuenta_id": "41095c46-babd-53a1-a59b-231da3e9d99a",
    "elementos": [],
    "pagina": 1,
    "por_pagina": 5,
    "total": 0,
    "version_motor": "motor-pagos/v1"
  },
  "resumen": {
    "aviso": "Recuperacion interpretada por motor-pagos/v1 sobre las fuentes disponibles, no un saldo contable. Bruta: los pagos que ningun reverso anulo. Neta: la bruta menos los negativos que no se pudieron enlazar con su original. No cuenta duplicados exactos ni coincidencias ambiguas.",
    "coincidencias_ambiguas": 0,
    "duplicados_exactos": 0,
    "movimientos_canonicos": 0,
    "no_conciliados": 0,
    "observaciones": 0,
    "observaciones_interpretadas": 0,
    "pagos_anulados": 0,
    "posibles_reversos": 0,
    "recuperacion_bruta_interpretada": "0.00",
    "recuperacion_neta_interpretada": "0.00",
    "reversos": 0,
    "version_motor": "motor-pagos/v1"
  }
};

export const gestionesDeCuenta: Respuesta<"/app/v1/cuentas/{cuenta_id}/gestiones"> = {
  "aviso_atribucion": "Asociacion operacional; no implica causalidad.",
  "cuenta_id": "41095c46-babd-53a1-a59b-231da3e9d99a",
  "pagina": {
    "aviso": "Eventos operacionales que registra Motor Cartera: lo que la cobranza hizo, con su momento de negocio (ocurrido_en) y su momento de registro (registrado_en). No son una fuente oficial del acreedor, y lo que un corte dice de una promesa o de un plan es una observacion de la fuente, no un evento.",
    "cuenta_id": "41095c46-babd-53a1-a59b-231da3e9d99a",
    "elementos": [],
    "pagina": 1,
    "por_pagina": 5,
    "total": 0
  },
  "resumen": {
    "convenios": 0,
    "convenios_vigentes": 0,
    "gestiones": 0,
    "gestiones_anuladas": 0,
    "promesas": 0,
    "promesas_vigentes": 0,
    "ultima_atribucion": null,
    "ultima_gestion": null,
    "ultimo_contacto_titular": null,
    "version_lifecycle": "lifecycle/v1",
    "visitas": 0
  }
};

export const pagosConAsociacionUnica: Respuesta<"/app/v1/cuentas/{cuenta_id}/pagos"> = {
  "aviso_atribucion": "Asociacion operacional; no implica causalidad.",
  "cuenta_id": "faaf273a-70da-53e9-80e4-7bcdadcbf338",
  "pagina": {
    "aviso": "Asociacion operacional, no causalidad: dice que gestiones con contacto de la misma cuenta ocurrieron antes de un pago, dentro de la ventana de la ejecucion, no que una gestion lo haya producido. Con dos o mas candidatas el pago queda AMBIGUA y no se elige ninguna.",
    "cuenta_id": "faaf273a-70da-53e9-80e4-7bcdadcbf338",
    "elementos": [
      {
        "atribucion": {
          "anulado_por_reverso": false,
          "atribucion_run_id": "9a51bd71-09fb-4202-976d-21956a565faa",
          "candidatas": [
            {
              "antelacion_segundos": 86400,
              "canal": "TELEFONICA",
              "gestion_id": "1ee63e60-d824-43bd-b87e-25c3e11f7f62",
              "nivel_contacto": "CONTACTO_TITULAR",
              "ocurrido_en": "2026-09-23T06:40:34Z",
              "resultado": "PROMESA"
            }
          ],
          "clasificacion": "ASOCIACION_UNICA",
          "cuenta_id": "faaf273a-70da-53e9-80e4-7bcdadcbf338",
          "fecha_recepcion": "2026-09-24T00:40:34",
          "gestion_id": "1ee63e60-d824-43bd-b87e-25c3e11f7f62",
          "interpretacion_de_pagos_vigente": true,
          "monto": "91.76",
          "motivos": [
            {
              "codigo": "UNA_GESTION_CANDIDATA",
              "ventana_dias": 30
            }
          ],
          "motor_pagos_run_id": "99b8c5a6-c05b-44eb-8699-a23ed11d1e14",
          "movimiento_id": "da041e29-f2f3-8f7d-985d-6165298a2af2",
          "ventana_dias": 30,
          "vigente": true
        },
        "movimiento": {
          "anulado_por_movimiento_id": null,
          "cuenta_id": "faaf273a-70da-53e9-80e4-7bcdadcbf338",
          "estado_conciliacion": "CONCILIADO_CUENTA",
          "fecha_recepcion": "2026-09-24T00:40:34",
          "firma_exacta": "ad80747c525cff182b817a9915e3e5b97bfc5aa0062c29eab9209608706146e6",
          "monto_reportado": "91.76",
          "motor_pagos_run_id": "99b8c5a6-c05b-44eb-8699-a23ed11d1e14",
          "movimiento_id": "da041e29-f2f3-8f7d-985d-6165298a2af2",
          "movimiento_original_id": null,
          "observaciones": 1,
          "periodo": "2026-09",
          "signo_economico": "SUMA",
          "tipo_movimiento": "PAGO",
          "version_motor": "motor-pagos/v1",
          "vigente": true
        }
      }
    ],
    "pagina": 1,
    "por_pagina": 5,
    "total": 1,
    "version_motor": "motor-pagos/v1"
  },
  "resumen": {
    "aviso": "Recuperacion interpretada por motor-pagos/v1 sobre las fuentes disponibles, no un saldo contable. Bruta: los pagos que ningun reverso anulo. Neta: la bruta menos los negativos que no se pudieron enlazar con su original. No cuenta duplicados exactos ni coincidencias ambiguas.",
    "coincidencias_ambiguas": 0,
    "duplicados_exactos": 0,
    "movimientos_canonicos": 1,
    "no_conciliados": 0,
    "observaciones": 1,
    "observaciones_interpretadas": 1,
    "pagos_anulados": 0,
    "posibles_reversos": 0,
    "recuperacion_bruta_interpretada": "91.76",
    "recuperacion_neta_interpretada": "91.76",
    "reversos": 0,
    "version_motor": "motor-pagos/v1"
  }
};

export const pagosAmbiguos: Respuesta<"/app/v1/cuentas/{cuenta_id}/pagos"> = {
  "aviso_atribucion": "Asociacion operacional; no implica causalidad.",
  "cuenta_id": "c1bb97cb-40f1-597b-953f-c8c794987dc7",
  "pagina": {
    "aviso": "Asociacion operacional, no causalidad: dice que gestiones con contacto de la misma cuenta ocurrieron antes de un pago, dentro de la ventana de la ejecucion, no que una gestion lo haya producido. Con dos o mas candidatas el pago queda AMBIGUA y no se elige ninguna.",
    "cuenta_id": "c1bb97cb-40f1-597b-953f-c8c794987dc7",
    "elementos": [
      {
        "atribucion": {
          "anulado_por_reverso": false,
          "atribucion_run_id": "9a51bd71-09fb-4202-976d-21956a565faa",
          "candidatas": [
            {
              "antelacion_segundos": 86400,
              "canal": "TELEFONICA",
              "gestion_id": "deaf1b8f-c657-4d2d-9e23-88e8502b3542",
              "nivel_contacto": "CONTACTO_TERCERO",
              "ocurrido_en": "2026-09-23T06:43:37Z",
              "resultado": "CONTACTO"
            },
            {
              "antelacion_segundos": 172800,
              "canal": "TELEFONICA",
              "gestion_id": "3af77c88-fd76-4aa8-bb1a-6da5f9f4010d",
              "nivel_contacto": "CONTACTO_TITULAR",
              "ocurrido_en": "2026-09-22T06:43:37Z",
              "resultado": "CONTACTO"
            }
          ],
          "clasificacion": "AMBIGUA",
          "cuenta_id": "c1bb97cb-40f1-597b-953f-c8c794987dc7",
          "fecha_recepcion": "2026-09-24T00:43:37",
          "gestion_id": null,
          "interpretacion_de_pagos_vigente": true,
          "monto": "762.51",
          "motivos": [
            {
              "candidatas": 2,
              "codigo": "VARIAS_GESTIONES_CANDIDATAS",
              "ventana_dias": 30
            }
          ],
          "motor_pagos_run_id": "99b8c5a6-c05b-44eb-8699-a23ed11d1e14",
          "movimiento_id": "ff60472e-14c5-8483-9dc6-810cc8918e4f",
          "ventana_dias": 30,
          "vigente": true
        },
        "movimiento": {
          "anulado_por_movimiento_id": null,
          "cuenta_id": "c1bb97cb-40f1-597b-953f-c8c794987dc7",
          "estado_conciliacion": "CONCILIADO_CUENTA",
          "fecha_recepcion": "2026-09-24T00:43:37",
          "firma_exacta": "414a4e888ea816c191237b7000ed0e81c257723e672c3b48a6d7fe07aa504f99",
          "monto_reportado": "762.51",
          "motor_pagos_run_id": "99b8c5a6-c05b-44eb-8699-a23ed11d1e14",
          "movimiento_id": "ff60472e-14c5-8483-9dc6-810cc8918e4f",
          "movimiento_original_id": null,
          "observaciones": 1,
          "periodo": "2026-09",
          "signo_economico": "SUMA",
          "tipo_movimiento": "PAGO",
          "version_motor": "motor-pagos/v1",
          "vigente": true
        }
      }
    ],
    "pagina": 1,
    "por_pagina": 5,
    "total": 1,
    "version_motor": "motor-pagos/v1"
  },
  "resumen": {
    "aviso": "Recuperacion interpretada por motor-pagos/v1 sobre las fuentes disponibles, no un saldo contable. Bruta: los pagos que ningun reverso anulo. Neta: la bruta menos los negativos que no se pudieron enlazar con su original. No cuenta duplicados exactos ni coincidencias ambiguas.",
    "coincidencias_ambiguas": 0,
    "duplicados_exactos": 0,
    "movimientos_canonicos": 1,
    "no_conciliados": 0,
    "observaciones": 1,
    "observaciones_interpretadas": 1,
    "pagos_anulados": 0,
    "posibles_reversos": 0,
    "recuperacion_bruta_interpretada": "762.51",
    "recuperacion_neta_interpretada": "762.51",
    "reversos": 0,
    "version_motor": "motor-pagos/v1"
  }
};

export const lineaDeTiempoConEventos: Respuesta<"/app/v1/cuentas/{cuenta_id}/linea-de-tiempo"> = {
  "aviso": "Eventos operacionales que registra Motor Cartera: lo que la cobranza hizo, con su momento de negocio (ocurrido_en) y su momento de registro (registrado_en). No son una fuente oficial del acreedor, y lo que un corte dice de una promesa o de un plan es una observacion de la fuente, no un evento.",
  "cuenta_id": "a27f64a5-895a-516c-9fe6-b8e13d03dd1d",
  "elementos": [
    {
      "dominio": "OPERACIONAL",
      "economico": null,
      "fuente_corte": null,
      "instante": "2026-07-13T21:31:19Z",
      "operacional": {
        "canal": null,
        "convenio_id": null,
        "evento": {
          "evento_id": "8d469343-4fbb-42e5-a992-53947ffab920",
          "evento_relacionado_id": "a4d7dc7c-2d18-4261-abc9-43ee2a0be16f",
          "motivo": null,
          "ocurrido_en": "2026-07-13T21:31:19Z",
          "origen_registro": "IMPORTACION",
          "registrado_en": "2026-10-17T13:16:22.459678Z",
          "tipo_evento": "PROMESA_CREADA",
          "version_evento": "lifecycle/v1"
        },
        "fecha_limite": "2026-07-23",
        "gestion_id": null,
        "monto_prometido": "284.82",
        "monto_total_acordado": null,
        "nivel_contacto": null,
        "promesa_id": "9b15b192-4386-4b88-899a-596387e22cd9",
        "resultado": null
      },
      "tipo": "PROMESA_CREADA"
    },
    {
      "dominio": "OPERACIONAL",
      "economico": null,
      "fuente_corte": null,
      "instante": "2026-07-13T21:31:19Z",
      "operacional": {
        "canal": "TELEFONICA",
        "convenio_id": null,
        "evento": {
          "evento_id": "a4d7dc7c-2d18-4261-abc9-43ee2a0be16f",
          "evento_relacionado_id": null,
          "motivo": null,
          "ocurrido_en": "2026-07-13T21:31:19Z",
          "origen_registro": "IMPORTACION",
          "registrado_en": "2026-10-17T13:16:22.459678Z",
          "tipo_evento": "GESTION_REGISTRADA",
          "version_evento": "lifecycle/v1"
        },
        "fecha_limite": null,
        "gestion_id": "50c90914-c411-45b0-8ad5-3c244e45c23a",
        "monto_prometido": null,
        "monto_total_acordado": null,
        "nivel_contacto": "CONTACTO_TITULAR",
        "promesa_id": null,
        "resultado": "PROMESA"
      },
      "tipo": "GESTION_REGISTRADA"
    },
    {
      "dominio": "OPERACIONAL",
      "economico": null,
      "fuente_corte": null,
      "instante": "2026-07-06T01:18:25Z",
      "operacional": {
        "canal": null,
        "convenio_id": null,
        "evento": {
          "evento_id": "6c3489ba-40e6-44b2-85e0-96d102d6c100",
          "evento_relacionado_id": "dda2a80a-c80d-452a-b701-f3ca06daebde",
          "motivo": null,
          "ocurrido_en": "2026-07-06T01:18:25Z",
          "origen_registro": "IMPORTACION",
          "registrado_en": "2026-10-17T13:16:20.709152Z",
          "tipo_evento": "PROMESA_CREADA",
          "version_evento": "lifecycle/v1"
        },
        "fecha_limite": "2026-07-13",
        "gestion_id": null,
        "monto_prometido": "155.42",
        "monto_total_acordado": null,
        "nivel_contacto": null,
        "promesa_id": "f41ee03c-de9c-4048-924b-3d26ff486aaf",
        "resultado": null
      },
      "tipo": "PROMESA_CREADA"
    },
    {
      "dominio": "OPERACIONAL",
      "economico": null,
      "fuente_corte": null,
      "instante": "2026-07-06T01:18:25Z",
      "operacional": {
        "canal": "CAMPO",
        "convenio_id": null,
        "evento": {
          "evento_id": "dda2a80a-c80d-452a-b701-f3ca06daebde",
          "evento_relacionado_id": null,
          "motivo": null,
          "ocurrido_en": "2026-07-06T01:18:25Z",
          "origen_registro": "IMPORTACION",
          "registrado_en": "2026-10-17T13:16:20.709152Z",
          "tipo_evento": "GESTION_REGISTRADA",
          "version_evento": "lifecycle/v1"
        },
        "fecha_limite": null,
        "gestion_id": "2428323f-b6f2-4e15-a804-5e662909ddd4",
        "monto_prometido": null,
        "monto_total_acordado": null,
        "nivel_contacto": "CONTACTO_TITULAR",
        "promesa_id": null,
        "resultado": "PROMESA"
      },
      "tipo": "GESTION_REGISTRADA"
    },
    {
      "dominio": "OPERACIONAL",
      "economico": null,
      "fuente_corte": null,
      "instante": "2026-06-29T22:46:18Z",
      "operacional": {
        "canal": null,
        "convenio_id": null,
        "evento": {
          "evento_id": "1639125a-1814-44f0-bc3c-4dd62017136e",
          "evento_relacionado_id": "c35b16c6-a1da-4fb1-bcd3-37a8c0cd6cf4",
          "motivo": null,
          "ocurrido_en": "2026-06-29T22:46:18Z",
          "origen_registro": "IMPORTACION",
          "registrado_en": "2026-10-17T13:16:18.988093Z",
          "tipo_evento": "PROMESA_CREADA",
          "version_evento": "lifecycle/v1"
        },
        "fecha_limite": "2026-07-04",
        "gestion_id": null,
        "monto_prometido": "204.01",
        "monto_total_acordado": null,
        "nivel_contacto": null,
        "promesa_id": "c0587180-e771-442b-b1bd-f83a2f981cf3",
        "resultado": null
      },
      "tipo": "PROMESA_CREADA"
    }
  ],
  "orden": "desc",
  "pagina": 1,
  "por_pagina": 5,
  "total": 32,
  "version_motor": "motor-pagos/v1"
};

export const gestionesConEventos: Respuesta<"/app/v1/cuentas/{cuenta_id}/gestiones"> = {
  "aviso_atribucion": "Asociacion operacional; no implica causalidad.",
  "cuenta_id": "a27f64a5-895a-516c-9fe6-b8e13d03dd1d",
  "pagina": {
    "aviso": "Eventos operacionales que registra Motor Cartera: lo que la cobranza hizo, con su momento de negocio (ocurrido_en) y su momento de registro (registrado_en). No son una fuente oficial del acreedor, y lo que un corte dice de una promesa o de un plan es una observacion de la fuente, no un evento.",
    "cuenta_id": "a27f64a5-895a-516c-9fe6-b8e13d03dd1d",
    "elementos": [
      {
        "anulacion": null,
        "canal": "TELEFONICA",
        "convenio_id": null,
        "cuenta_id": "a27f64a5-895a-516c-9fe6-b8e13d03dd1d",
        "estado": "VIGENTE",
        "evento": {
          "evento_id": "a4d7dc7c-2d18-4261-abc9-43ee2a0be16f",
          "evento_relacionado_id": null,
          "motivo": null,
          "ocurrido_en": "2026-07-13T21:31:19Z",
          "origen_registro": "IMPORTACION",
          "registrado_en": "2026-10-17T13:16:22.459678Z",
          "tipo_evento": "GESTION_REGISTRADA",
          "version_evento": "lifecycle/v1"
        },
        "gestion_id": "50c90914-c411-45b0-8ad5-3c244e45c23a",
        "medio": "LLAMADA",
        "nivel_contacto": "CONTACTO_TITULAR",
        "ocurrido_en": "2026-07-13T21:31:19Z",
        "promesa_id": "9b15b192-4386-4b88-899a-596387e22cd9",
        "registrado_en": "2026-10-17T13:16:22.459678Z",
        "resultado": "PROMESA",
        "visita": null
      },
      {
        "anulacion": null,
        "canal": "CAMPO",
        "convenio_id": null,
        "cuenta_id": "a27f64a5-895a-516c-9fe6-b8e13d03dd1d",
        "estado": "VIGENTE",
        "evento": {
          "evento_id": "dda2a80a-c80d-452a-b701-f3ca06daebde",
          "evento_relacionado_id": null,
          "motivo": null,
          "ocurrido_en": "2026-07-06T01:18:25Z",
          "origen_registro": "IMPORTACION",
          "registrado_en": "2026-10-17T13:16:20.709152Z",
          "tipo_evento": "GESTION_REGISTRADA",
          "version_evento": "lifecycle/v1"
        },
        "gestion_id": "2428323f-b6f2-4e15-a804-5e662909ddd4",
        "medio": null,
        "nivel_contacto": "CONTACTO_TITULAR",
        "ocurrido_en": "2026-07-06T01:18:25Z",
        "promesa_id": "f41ee03c-de9c-4048-924b-3d26ff486aaf",
        "registrado_en": "2026-10-17T13:16:20.709152Z",
        "resultado": "PROMESA",
        "visita": {
          "fin": "2026-07-06T01:36:08Z",
          "inicio": "2026-07-06T00:50:32Z",
          "resultado": "CONTACTO_TITULAR",
          "visita_id": "41cf7513-6b92-4c4b-ba3d-32048dd27695"
        }
      },
      {
        "anulacion": null,
        "canal": "TELEFONICA",
        "convenio_id": null,
        "cuenta_id": "a27f64a5-895a-516c-9fe6-b8e13d03dd1d",
        "estado": "VIGENTE",
        "evento": {
          "evento_id": "c35b16c6-a1da-4fb1-bcd3-37a8c0cd6cf4",
          "evento_relacionado_id": null,
          "motivo": null,
          "ocurrido_en": "2026-06-29T22:46:18Z",
          "origen_registro": "IMPORTACION",
          "registrado_en": "2026-10-17T13:16:18.988093Z",
          "tipo_evento": "GESTION_REGISTRADA",
          "version_evento": "lifecycle/v1"
        },
        "gestion_id": "c13fd29c-bc2c-4385-a7fd-74f1ba78752e",
        "medio": "LLAMADA",
        "nivel_contacto": "CONTACTO_TITULAR",
        "ocurrido_en": "2026-06-29T22:46:18Z",
        "promesa_id": "c0587180-e771-442b-b1bd-f83a2f981cf3",
        "registrado_en": "2026-10-17T13:16:18.988093Z",
        "resultado": "PROMESA",
        "visita": null
      },
      {
        "anulacion": null,
        "canal": "TELEFONICA",
        "convenio_id": null,
        "cuenta_id": "a27f64a5-895a-516c-9fe6-b8e13d03dd1d",
        "estado": "VIGENTE",
        "evento": {
          "evento_id": "744752e8-4d78-4ed4-8e48-1ba264da1585",
          "evento_relacionado_id": null,
          "motivo": null,
          "ocurrido_en": "2026-06-29T22:36:20Z",
          "origen_registro": "IMPORTACION",
          "registrado_en": "2026-10-17T13:16:18.988093Z",
          "tipo_evento": "GESTION_REGISTRADA",
          "version_evento": "lifecycle/v1"
        },
        "gestion_id": "8857af37-c458-4403-96c0-6ddc2f7216dc",
        "medio": "LLAMADA",
        "nivel_contacto": "CONTACTO_TITULAR",
        "ocurrido_en": "2026-06-29T22:36:20Z",
        "promesa_id": "f318ee6b-9a13-407c-8f9f-7161242cfd1e",
        "registrado_en": "2026-10-17T13:16:18.988093Z",
        "resultado": "PROMESA",
        "visita": null
      },
      {
        "anulacion": null,
        "canal": "CAMPO",
        "convenio_id": null,
        "cuenta_id": "a27f64a5-895a-516c-9fe6-b8e13d03dd1d",
        "estado": "VIGENTE",
        "evento": {
          "evento_id": "fddf3be6-3665-44e9-b2a0-068a57923d7c",
          "evento_relacionado_id": null,
          "motivo": null,
          "ocurrido_en": "2026-06-29T13:39:14Z",
          "origen_registro": "IMPORTACION",
          "registrado_en": "2026-10-17T13:16:18.988093Z",
          "tipo_evento": "GESTION_REGISTRADA",
          "version_evento": "lifecycle/v1"
        },
        "gestion_id": "af52d038-c677-4046-b47e-9cafe6ccdcc9",
        "medio": null,
        "nivel_contacto": "CONTACTO_TERCERO",
        "ocurrido_en": "2026-06-29T13:39:14Z",
        "promesa_id": null,
        "registrado_en": "2026-10-17T13:16:18.988093Z",
        "resultado": "VISITA_REALIZADA",
        "visita": {
          "fin": "2026-06-29T13:46:24Z",
          "inicio": "2026-06-29T13:28:40Z",
          "resultado": "CONTACTO_TERCERO",
          "visita_id": "19d3f684-3b52-4c91-8431-fe3036338a8e"
        }
      }
    ],
    "pagina": 1,
    "por_pagina": 5,
    "total": 22
  },
  "resumen": {
    "convenios": 1,
    "convenios_vigentes": 1,
    "gestiones": 21,
    "gestiones_anuladas": 1,
    "promesas": 6,
    "promesas_vigentes": 6,
    "ultima_atribucion": null,
    "ultima_gestion": {
      "canal": "TELEFONICA",
      "gestion_id": "50c90914-c411-45b0-8ad5-3c244e45c23a",
      "nivel_contacto": "CONTACTO_TITULAR",
      "ocurrido_en": "2026-07-13T21:31:19Z",
      "resultado": "PROMESA"
    },
    "ultimo_contacto_titular": {
      "canal": "TELEFONICA",
      "gestion_id": "50c90914-c411-45b0-8ad5-3c244e45c23a",
      "nivel_contacto": "CONTACTO_TITULAR",
      "ocurrido_en": "2026-07-13T21:31:19Z",
      "resultado": "PROMESA"
    },
    "version_lifecycle": "lifecycle/v1",
    "visitas": 13
  }
};

export const linaje: Respuesta<"/app/v1/linaje"> = {
  "aristas": [
    {
      "desde": "DECISION_V2:13c166a9-218f-435d-a36c-5aea9eaee03e",
      "hacia": "CORTE:8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "relacion": "es del corte"
    },
    {
      "desde": "SCORING:c1397868-406c-431b-ae0a-25a791e574ae",
      "hacia": "CORTE:8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "relacion": "es del corte"
    },
    {
      "desde": "SCORING:c1397868-406c-431b-ae0a-25a791e574ae",
      "hacia": "MODELO:261a485b-7f86-44f9-808a-190f164fd471",
      "relacion": "puntuo con esa version del modelo"
    },
    {
      "desde": "OPTIMIZACION:bb5fda47-20a1-4bf9-a466-69102e76b965",
      "hacia": "CORTE:8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "relacion": "es del corte"
    },
    {
      "desde": "OPTIMIZACION:bb5fda47-20a1-4bf9-a466-69102e76b965",
      "hacia": "CAMPO_V2:581de30e-9e76-4b3a-a722-bf0289e1b25b",
      "relacion": "tomo los gestores de ese plan de campo"
    },
    {
      "desde": "OPTIMIZACION:bb5fda47-20a1-4bf9-a466-69102e76b965",
      "hacia": "CONFIGURACION_OPTIMIZACION:ec6c6274-a2a0-4999-b2f9-5721c78c5bda",
      "relacion": "con esa configuracion"
    },
    {
      "desde": "OPTIMIZACION:bb5fda47-20a1-4bf9-a466-69102e76b965",
      "hacia": "DECISION_V2:13c166a9-218f-435d-a36c-5aea9eaee03e",
      "relacion": "tomo el canal y la prioridad de esa decision/v2"
    },
    {
      "desde": "OPTIMIZACION:bb5fda47-20a1-4bf9-a466-69102e76b965",
      "hacia": "RUTEO_V2:4730febb-aa0c-4372-8918-04387d31fe71",
      "relacion": "tomo las paradas PROGRAMADA de ese ruteo"
    },
    {
      "desde": "OPTIMIZACION:bb5fda47-20a1-4bf9-a466-69102e76b965",
      "hacia": "SCORING:c1397868-406c-431b-ae0a-25a791e574ae",
      "relacion": "optimizo el valor de oportunidad de ese scoring"
    },
    {
      "desde": "CAMPO_V2:581de30e-9e76-4b3a-a722-bf0289e1b25b",
      "hacia": "CORTE:8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "relacion": "es del corte"
    },
    {
      "desde": "CAMPO_V2:581de30e-9e76-4b3a-a722-bf0289e1b25b",
      "hacia": "CONFIGURACION_CAMPO:4cece266-3c7a-4ff1-bc62-d2a64989e9ac",
      "relacion": "con esa configuracion de campo"
    },
    {
      "desde": "CAMPO_V2:581de30e-9e76-4b3a-a722-bf0289e1b25b",
      "hacia": "DECISION_V2:13c166a9-218f-435d-a36c-5aea9eaee03e",
      "relacion": "planeo las candidatas de esa decision/v2"
    },
    {
      "desde": "CAMPO_V2:581de30e-9e76-4b3a-a722-bf0289e1b25b",
      "hacia": "TERRITORIAL_V2:d89b3565-7416-47a0-b4e7-e5bf22bc3e0b",
      "relacion": "con las bases de esa territorial/v2"
    },
    {
      "desde": "RUTEO_V2:4730febb-aa0c-4372-8918-04387d31fe71",
      "hacia": "CAMPO_V2:581de30e-9e76-4b3a-a722-bf0289e1b25b",
      "relacion": "ruteo ese plan de campo"
    },
    {
      "desde": "RUTEO_V2:4730febb-aa0c-4372-8918-04387d31fe71",
      "hacia": "RED_VIAL:84d173b2-0b93-4838-97ac-2af88b18e5fc",
      "relacion": "sobre esa red vial"
    },
    {
      "desde": "TERRITORIAL_V2:d89b3565-7416-47a0-b4e7-e5bf22bc3e0b",
      "hacia": "GEOGRAFIA:7455fff1-9fd1-4b8b-a217-cbfd1865f6e4",
      "relacion": "asigno sobre esa geografia"
    },
    {
      "desde": "GEOGRAFIA:7455fff1-9fd1-4b8b-a217-cbfd1865f6e4",
      "hacia": "CORTE:8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "relacion": "es del corte"
    },
    {
      "desde": "MODELO:261a485b-7f86-44f9-808a-190f164fd471",
      "hacia": "ENTRENAMIENTO:fed27c20-9a7d-433e-829d-647c29f62d52",
      "relacion": "la publico ese entrenamiento"
    },
    {
      "desde": "ANALYTICS:f2c5b154-a3f3-4105-94a0-f14032d00e37",
      "hacia": "ATRIBUCION:9a51bd71-09fb-4202-976d-21956a565faa",
      "relacion": "la leyo su manifiesto"
    },
    {
      "desde": "ANALYTICS:f2c5b154-a3f3-4105-94a0-f14032d00e37",
      "hacia": "CORTE:8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "relacion": "la leyo su manifiesto"
    },
    {
      "desde": "ANALYTICS:f2c5b154-a3f3-4105-94a0-f14032d00e37",
      "hacia": "DECISION_V2:13c166a9-218f-435d-a36c-5aea9eaee03e",
      "relacion": "la leyo su manifiesto"
    },
    {
      "desde": "ANALYTICS:f2c5b154-a3f3-4105-94a0-f14032d00e37",
      "hacia": "EVALUACION_PROMESAS:d40af884-dfcd-461c-9efb-b2d1cef1930d",
      "relacion": "la leyo su manifiesto"
    },
    {
      "desde": "ANALYTICS:f2c5b154-a3f3-4105-94a0-f14032d00e37",
      "hacia": "GEOGRAFIA:7455fff1-9fd1-4b8b-a217-cbfd1865f6e4",
      "relacion": "la leyo su manifiesto"
    },
    {
      "desde": "ANALYTICS:f2c5b154-a3f3-4105-94a0-f14032d00e37",
      "hacia": "MOTOR_PAGOS:99b8c5a6-c05b-44eb-8699-a23ed11d1e14",
      "relacion": "la leyo su manifiesto"
    },
    {
      "desde": "ANALYTICS:f2c5b154-a3f3-4105-94a0-f14032d00e37",
      "hacia": "MOTOR_PAGOS:9cc3041a-5c91-435f-9572-8e284a1a8358",
      "relacion": "la leyo su manifiesto"
    }
  ],
  "aviso": "app/v1 es de solo lectura: compone lo que cada motor ya publico, bajo un contexto explicito (el corte y cada ejecucion, por su identificador publico). No ejecuta, no envia, no llama, no visita, no optimiza ni reentrena nada, y nada se elige como 'lo ultimo'.",
  "compatible": true,
  "nodos": [
    {
      "as_of": "2026-09-30",
      "clave": "CORTE:8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "declarado": true,
      "estado": null,
      "fecha_operacion": null,
      "hashes": {
        "firma_contenido": "a5c54444360efe7c1619339ec5cf6ceff19c1917ed38bccf39b042a587f2def5"
      },
      "iniciada_en": "2026-10-17T13:14:16.920183Z",
      "known_at": null,
      "resultado": null,
      "run_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "terminada_en": null,
      "tipo": "CORTE",
      "version": "historia/v1",
      "version_politica": null
    },
    {
      "as_of": "2026-09-30",
      "clave": "DECISION_V2:13c166a9-218f-435d-a36c-5aea9eaee03e",
      "declarado": true,
      "estado": "EXITOSA",
      "fecha_operacion": null,
      "hashes": {
        "firma_inputs": "b30987e15bb8cec05f12bc4019393cd5386e53224566dd7fa8c24fa908e8036d",
        "hash_politica": "2f9687400863f6af49a3c0fc51fe10c6c3b6f080804fcd4ef643ddad641d29e3"
      },
      "iniciada_en": "2026-10-17T13:14:29.506615Z",
      "known_at": "2026-10-17T13:14:29.444442Z",
      "resultado": "DECISION_PUBLICADA",
      "run_id": "13c166a9-218f-435d-a36c-5aea9eaee03e",
      "terminada_en": "2026-10-17T13:14:30.301800Z",
      "tipo": "DECISION_V2",
      "version": "decision/v2",
      "version_politica": "decision-policy/public-v1"
    },
    {
      "as_of": "2026-10-01",
      "clave": "ANALYTICS:f2c5b154-a3f3-4105-94a0-f14032d00e37",
      "declarado": true,
      "estado": "EXITOSA",
      "fecha_operacion": null,
      "hashes": {
        "firma_inputs": "394a082a72e220a33a0e0da2300d4358e90d37d071e8f487573a8db73b5fa12a",
        "hash_politica": "3529b0893889bf8b28491c7fd2671cd3961b83915ba83084146585351fcb8a80"
      },
      "iniciada_en": "2026-10-17T13:15:41.444118Z",
      "known_at": "2026-10-17T13:15:35.619443Z",
      "resultado": "ANALYTICS_PUBLICADA",
      "run_id": "f2c5b154-a3f3-4105-94a0-f14032d00e37",
      "terminada_en": "2026-10-17T13:15:42.502268Z",
      "tipo": "ANALYTICS",
      "version": "analytics/v1",
      "version_politica": "analytics-public-v1"
    },
    {
      "as_of": "2026-09-30",
      "clave": "SCORING:c1397868-406c-431b-ae0a-25a791e574ae",
      "declarado": true,
      "estado": "EXITOSA",
      "fecha_operacion": null,
      "hashes": {
        "firma_inputs": "7cff352311bff21c8da67f7c8df64ee81ac8182fad3715de4322a1e848a6a3dd",
        "hash_politica": "72630f625de5bc53b777da286395474b857d641b810e792c8cb595159973bfe1"
      },
      "iniciada_en": "2026-10-17T13:16:43.369953Z",
      "known_at": "2026-10-17T13:14:29.444442Z",
      "resultado": "PREDICCIONES_PUBLICADAS",
      "run_id": "c1397868-406c-431b-ae0a-25a791e574ae",
      "terminada_en": "2026-10-17T13:16:43.996389Z",
      "tipo": "SCORING",
      "version": "predictive/model-v1",
      "version_politica": "predictive-training/public-v1"
    },
    {
      "as_of": null,
      "clave": "MODELO:261a485b-7f86-44f9-808a-190f164fd471",
      "declarado": false,
      "estado": "PUBLICADA",
      "fecha_operacion": null,
      "hashes": {
        "dataset_hash": "63531824e99322b217a927f3eb21fdca1e4059ad5d756ee441aa7f39eeb5b79e",
        "hash_features": "786b5ef4b5a21a7583f59082196f77bc4900636ed1089e749fe3a726f9db5e94",
        "hash_modelo": "8540230a4df7febc73aab018a11182c56d5fb98166004bacbebebdb334b67b74",
        "hash_politica": "72630f625de5bc53b777da286395474b857d641b810e792c8cb595159973bfe1",
        "hash_targets": "bc76c4c5b0d60f2f3b628d9f2c8d0b54bdb670c5bc4aace236276ba884cde5b2"
      },
      "iniciada_en": "2026-10-17T13:16:35.949306Z",
      "known_at": null,
      "resultado": null,
      "run_id": "261a485b-7f86-44f9-808a-190f164fd471",
      "terminada_en": "2026-10-17T13:16:35.981951Z",
      "tipo": "MODELO",
      "version": "predictive/model-v1",
      "version_politica": "predictive-training/public-v1"
    },
    {
      "as_of": null,
      "clave": "OPTIMIZACION:bb5fda47-20a1-4bf9-a466-69102e76b965",
      "declarado": true,
      "estado": "EXITOSA",
      "fecha_operacion": "2026-10-01",
      "hashes": {
        "firma_inputs": "1526f329dfdd26da67b3ec4c59e3857985e2a1574774f1d03d2a2ec0f676fd77",
        "hash_politica": "540bc21fadd8297c2a8d427e8af540610642016e7dea5e9be87f997c53d23fc5"
      },
      "iniciada_en": "2026-10-17T13:16:44.547155Z",
      "known_at": null,
      "resultado": "PLAN_PUBLICADO",
      "run_id": "bb5fda47-20a1-4bf9-a466-69102e76b965",
      "terminada_en": "2026-10-17T13:16:45.184838Z",
      "tipo": "OPTIMIZACION",
      "version": "optimization/v1",
      "version_politica": "optimization-public-v1"
    },
    {
      "as_of": null,
      "clave": "CAMPO_V2:581de30e-9e76-4b3a-a722-bf0289e1b25b",
      "declarado": false,
      "estado": null,
      "fecha_operacion": null,
      "hashes": {},
      "iniciada_en": null,
      "known_at": null,
      "resultado": null,
      "run_id": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
      "terminada_en": null,
      "tipo": "CAMPO_V2",
      "version": null,
      "version_politica": null
    },
    {
      "as_of": null,
      "clave": "CONFIGURACION_OPTIMIZACION:ec6c6274-a2a0-4999-b2f9-5721c78c5bda",
      "declarado": false,
      "estado": "PUBLICADA",
      "fecha_operacion": null,
      "hashes": {
        "hash_contenido": "2bf5ffa23f64bbf9daac085582645ea197fc93ea1ebad73ff72e8dccaa232e47"
      },
      "iniciada_en": "2026-10-17T13:16:44.472587Z",
      "known_at": null,
      "resultado": null,
      "run_id": "ec6c6274-a2a0-4999-b2f9-5721c78c5bda",
      "terminada_en": "2026-10-17T13:16:44.504132Z",
      "tipo": "CONFIGURACION_OPTIMIZACION",
      "version": "optimization-config/v1",
      "version_politica": null
    },
    {
      "as_of": null,
      "clave": "RUTEO_V2:4730febb-aa0c-4372-8918-04387d31fe71",
      "declarado": false,
      "estado": null,
      "fecha_operacion": null,
      "hashes": {},
      "iniciada_en": null,
      "known_at": null,
      "resultado": null,
      "run_id": "4730febb-aa0c-4372-8918-04387d31fe71",
      "terminada_en": null,
      "tipo": "RUTEO_V2",
      "version": null,
      "version_politica": null
    },
    {
      "as_of": null,
      "clave": "CONFIGURACION_CAMPO:4cece266-3c7a-4ff1-bc62-d2a64989e9ac",
      "declarado": false,
      "estado": "PUBLICADA",
      "fecha_operacion": null,
      "hashes": {
        "hash_configuracion": "900d22efd85fffd64c6454d570625938d5033f4cf107551a2e1ba1e60d73354a"
      },
      "iniciada_en": "2026-10-17T13:14:39.280190Z",
      "known_at": null,
      "resultado": null,
      "run_id": "4cece266-3c7a-4ff1-bc62-d2a64989e9ac",
      "terminada_en": "2026-10-17T13:14:39.280190Z",
      "tipo": "CONFIGURACION_CAMPO",
      "version": "campo-configuracion/v1",
      "version_politica": null
    },
    {
      "as_of": null,
      "clave": "TERRITORIAL_V2:d89b3565-7416-47a0-b4e7-e5bf22bc3e0b",
      "declarado": false,
      "estado": "EXITOSA",
      "fecha_operacion": null,
      "hashes": {
        "firma_inputs": "49c7977f4009a6ff3fe60a61b399cf77cac64117b131ee45729a19fc98dac1d5",
        "hash_politica": "b233e9de169bf50add564f4ddcef3b6f93b2bd263a03171959fd7f1c321f4e22"
      },
      "iniciada_en": "2026-10-17T13:14:37.884169Z",
      "known_at": null,
      "resultado": "TERRITORIAL_PUBLICADO",
      "run_id": "d89b3565-7416-47a0-b4e7-e5bf22bc3e0b",
      "terminada_en": "2026-10-17T13:14:38.136751Z",
      "tipo": "TERRITORIAL_V2",
      "version": "territorial/v2",
      "version_politica": "territorial-public-v2"
    },
    {
      "as_of": null,
      "clave": "RED_VIAL:84d173b2-0b93-4838-97ac-2af88b18e5fc",
      "declarado": false,
      "estado": "PUBLICADA",
      "fecha_operacion": null,
      "hashes": {
        "huella": "1211063510392e330808ab038debded8817f2498d87057b13861bb056c5f29d8"
      },
      "iniciada_en": "2026-10-17T13:13:59.791097Z",
      "known_at": null,
      "resultado": null,
      "run_id": "84d173b2-0b93-4838-97ac-2af88b18e5fc",
      "terminada_en": "2026-10-17T13:14:00.071683Z",
      "tipo": "RED_VIAL",
      "version": "OSRM v26.9.0",
      "version_politica": null
    },
    {
      "as_of": null,
      "clave": "GEOGRAFIA:7455fff1-9fd1-4b8b-a217-cbfd1865f6e4",
      "declarado": false,
      "estado": "EXITOSA",
      "fecha_operacion": null,
      "hashes": {
        "firma_inputs": "46cbb941d61a65f1307fbaa3f88ca4883d5284bddadce9f7c5d7c26b559c1117",
        "sha256_referencia": "41332e3c8e499b10218e8c96ffc6b505365ad78c2bf93483857c951295c59e56"
      },
      "iniciada_en": "2026-10-17T13:14:31.017766Z",
      "known_at": null,
      "resultado": "GEOGRAFIA_PUBLICADA",
      "run_id": "7455fff1-9fd1-4b8b-a217-cbfd1865f6e4",
      "terminada_en": "2026-10-17T13:14:31.620108Z",
      "tipo": "GEOGRAFIA",
      "version": "geografia/v1",
      "version_politica": null
    },
    {
      "as_of": null,
      "clave": "ENTRENAMIENTO:fed27c20-9a7d-433e-829d-647c29f62d52",
      "declarado": false,
      "estado": "EXITOSA",
      "fecha_operacion": null,
      "hashes": {
        "firma_inputs": "37d07793d0fe22041fbef3cbdb784b2c865f8da5e2611e5bfa7bae10eb38df62",
        "hash_manifiesto": "24da163b996f5562f36fbe93a69b57f56aa08b323d918dc27475574b73ed129a"
      },
      "iniciada_en": "2026-10-17T13:16:28.994726Z",
      "known_at": null,
      "resultado": "MODELO_PUBLICADO",
      "run_id": "fed27c20-9a7d-433e-829d-647c29f62d52",
      "terminada_en": "2026-10-17T13:16:35.986682Z",
      "tipo": "ENTRENAMIENTO",
      "version": "predictive/model-v1",
      "version_politica": null
    },
    {
      "as_of": "2026-09-01",
      "clave": "ATRIBUCION:9a51bd71-09fb-4202-976d-21956a565faa",
      "declarado": false,
      "estado": null,
      "fecha_operacion": "2026-09-30",
      "hashes": {
        "firma": "fabd91d5960501480ba0e0118dc16f50927367346bb3ccad8a5859082fa2512d"
      },
      "iniciada_en": null,
      "known_at": null,
      "resultado": null,
      "run_id": "9a51bd71-09fb-4202-976d-21956a565faa",
      "terminada_en": "2026-10-17T13:14:28.468658Z",
      "tipo": "ATRIBUCION",
      "version": "atribucion/v1",
      "version_politica": null
    },
    {
      "as_of": "2026-10-01",
      "clave": "EVALUACION_PROMESAS:d40af884-dfcd-461c-9efb-b2d1cef1930d",
      "declarado": false,
      "estado": null,
      "fecha_operacion": "2026-10-01",
      "hashes": {
        "firma": "6e6f9d38c3977cd64cf4cbcc212327b8a7917b38dff37317932d6a02e7ba8552"
      },
      "iniciada_en": null,
      "known_at": null,
      "resultado": null,
      "run_id": "d40af884-dfcd-461c-9efb-b2d1cef1930d",
      "terminada_en": "2026-10-17T13:14:42.643089Z",
      "tipo": "EVALUACION_PROMESAS",
      "version": "evaluacion-promesa/v1",
      "version_politica": null
    },
    {
      "as_of": "2026-09-01",
      "clave": "MOTOR_PAGOS:99b8c5a6-c05b-44eb-8699-a23ed11d1e14",
      "declarado": false,
      "estado": null,
      "fecha_operacion": "2026-09-30",
      "hashes": {
        "firma": "3543debcfbabae19390c9301f3131b8e3334aa71e288a171a209bb1738096425"
      },
      "iniciada_en": null,
      "known_at": null,
      "resultado": null,
      "run_id": "99b8c5a6-c05b-44eb-8699-a23ed11d1e14",
      "terminada_en": "2026-10-17T13:14:18.104918Z",
      "tipo": "MOTOR_PAGOS",
      "version": "motor-pagos/v1",
      "version_politica": null
    },
    {
      "as_of": "2026-10-01",
      "clave": "MOTOR_PAGOS:9cc3041a-5c91-435f-9572-8e284a1a8358",
      "declarado": false,
      "estado": null,
      "fecha_operacion": "2026-10-31",
      "hashes": {
        "firma": "fdbe56bbbeb3f21b246ea0a3b5ef1b7b7f32223d4b3ffc8bc2ce8c09fc1b41c1"
      },
      "iniciada_en": null,
      "known_at": null,
      "resultado": null,
      "run_id": "9cc3041a-5c91-435f-9572-8e284a1a8358",
      "terminada_en": "2026-10-17T13:14:24.654257Z",
      "tipo": "MOTOR_PAGOS",
      "version": "motor-pagos/v1",
      "version_politica": null
    }
  ]
};

export const distribucion: Respuesta<"/app/v1/predictivo/scorings/{scoring_run_id}/distribucion"> = {
  "aviso": "Prediccion, no efecto causal ni recomendacion de accion.",
  "bins": 10,
  "con_imputacion": 1000,
  "histogramas": [
    {
      "cuentas": [
        3,
        120,
        126,
        118,
        155,
        329,
        140,
        9,
        0,
        0
      ],
      "salida": "p_recuperacion_positiva_30d"
    },
    {
      "cuentas": [
        3,
        152,
        392,
        443,
        10,
        0,
        0,
        0,
        0,
        0
      ],
      "salida": "p_contacto_14d"
    },
    {
      "cuentas": [
        0,
        328,
        672,
        0,
        0,
        0,
        0,
        0,
        0,
        0
      ],
      "salida": "p_promesa_30d"
    }
  ],
  "medias": {
    "p_contacto_14d": "0.27139607",
    "p_promesa_30d": "0.20593123",
    "p_recuperacion_positiva_30d": "0.44005066"
  },
  "modelo_version_id": "261a485b-7f86-44f9-808a-190f164fd471",
  "publicadas": 1000,
  "scoring_run_id": "c1397868-406c-431b-ae0a-25a791e574ae",
  "suma_recuperacion_positiva_esperada_30d": "1118947.24"
};

export const catalogo: Respuesta<"/analytics/catalogo"> = {
  "aviso": "analytics/v1 describe lo observado y lo planificado, agregado: no prueba causalidad, no predice y no recomienda. Una asociacion de la atribucion es operacional, no causal; una parada programada no es una visita realizada; un gestor de campo no es el actor_ref de una gestion. Las celdas con menos cuentas que el minimo de la politica se suprimen.",
  "conjuntos": [
    {
      "conjunto": "TOTAL",
      "llaves": [],
      "universos": [
        "CAMPO",
        "COHORTE",
        "RUTEO"
      ]
    },
    {
      "conjunto": "PRODUCTO",
      "llaves": [
        "producto"
      ],
      "universos": [
        "COHORTE"
      ]
    },
    {
      "conjunto": "ATRASO_BUCKET",
      "llaves": [
        "atraso_bucket"
      ],
      "universos": [
        "COHORTE"
      ]
    },
    {
      "conjunto": "PRIORIDAD_DECISION",
      "llaves": [
        "prioridad_decision"
      ],
      "universos": [
        "CAMPO",
        "COHORTE",
        "RUTEO"
      ]
    },
    {
      "conjunto": "CANAL_DECISION",
      "llaves": [
        "canal_decision"
      ],
      "universos": [
        "COHORTE"
      ]
    },
    {
      "conjunto": "ENTIDAD",
      "llaves": [
        "entidad"
      ],
      "universos": [
        "COHORTE"
      ]
    },
    {
      "conjunto": "MUNICIPIO",
      "llaves": [
        "municipio"
      ],
      "universos": [
        "COHORTE"
      ]
    },
    {
      "conjunto": "BASE",
      "llaves": [
        "base"
      ],
      "universos": [
        "CAMPO",
        "COHORTE",
        "RUTEO"
      ]
    },
    {
      "conjunto": "CANAL_GESTION",
      "llaves": [
        "canal_gestion"
      ],
      "universos": [
        "GESTION"
      ]
    },
    {
      "conjunto": "MEDIO_GESTION",
      "llaves": [
        "medio_gestion"
      ],
      "universos": [
        "GESTION"
      ]
    },
    {
      "conjunto": "ESTADO_CAMPO",
      "llaves": [
        "estado_campo"
      ],
      "universos": [
        "CAMPO"
      ]
    },
    {
      "conjunto": "ESTADO_RUTEO",
      "llaves": [
        "estado_ruteo"
      ],
      "universos": [
        "RUTEO"
      ]
    },
    {
      "conjunto": "GESTOR_CAMPO",
      "llaves": [
        "base",
        "gestor_campo"
      ],
      "universos": [
        "CAMPO",
        "RUTEO"
      ]
    },
    {
      "conjunto": "PRODUCTO_X_ATRASO",
      "llaves": [
        "producto",
        "atraso_bucket"
      ],
      "universos": [
        "COHORTE"
      ]
    },
    {
      "conjunto": "BASE_X_PRIORIDAD",
      "llaves": [
        "base",
        "prioridad_decision"
      ],
      "universos": [
        "CAMPO",
        "COHORTE"
      ]
    },
    {
      "conjunto": "CANAL_X_PRIORIDAD",
      "llaves": [
        "canal_decision",
        "prioridad_decision"
      ],
      "universos": [
        "COHORTE"
      ]
    }
  ],
  "hash": "553326f78c2ab17b48c8768af5e6dbb2c039f4b452aad092eb42b4113eacff64",
  "metricas": [
    {
      "aditiva": true,
      "agregacion": "CONTEO",
      "cobertura_de_pagos": false,
      "codigo": "cartera.cuentas",
      "denominador": null,
      "descripcion": "Cuantas cuentas tiene la cohorte, o la celda: las del corte declarado.",
      "dimensiones_permitidas": [
        {
          "conjunto": "TOTAL",
          "llaves": []
        },
        {
          "conjunto": "PRODUCTO",
          "llaves": [
            "producto"
          ]
        },
        {
          "conjunto": "ATRASO_BUCKET",
          "llaves": [
            "atraso_bucket"
          ]
        },
        {
          "conjunto": "PRIORIDAD_DECISION",
          "llaves": [
            "prioridad_decision"
          ]
        },
        {
          "conjunto": "CANAL_DECISION",
          "llaves": [
            "canal_decision"
          ]
        },
        {
          "conjunto": "ENTIDAD",
          "llaves": [
            "entidad"
          ]
        },
        {
          "conjunto": "MUNICIPIO",
          "llaves": [
            "municipio"
          ]
        },
        {
          "conjunto": "BASE",
          "llaves": [
            "base"
          ]
        },
        {
          "conjunto": "PRODUCTO_X_ATRASO",
          "llaves": [
            "producto",
            "atraso_bucket"
          ]
        },
        {
          "conjunto": "BASE_X_PRIORIDAD",
          "llaves": [
            "base",
            "prioridad_decision"
          ]
        },
        {
          "conjunto": "CANAL_X_PRIORIDAD",
          "llaves": [
            "canal_decision",
            "prioridad_decision"
          ]
        }
      ],
      "familia": "CARTERA",
      "formula": "count(cuentas de la cohorte)",
      "fuentes_requeridas": [
        "CORTE"
      ],
      "limitaciones": [],
      "monetaria": false,
      "nombre": "Cuentas de la cohorte",
      "nulo": "Nunca NO_DISPONIBLE: el corte es la entrada obligatoria.",
      "numerador": null,
      "poblacion": "Las cuentas de la cohorte: los snapshots del corte declarado, una vez cada una, con las dimensiones que tenian en el corte.",
      "puede_ser_negativa": false,
      "regla_temporal": "La foto del corte: el snapshot de cada cuenta en fecha_corte. No cambia con as_of ni con known_at: el corte se conocia a known_at (creado_en <= known_at), o la ejecucion no se abre.",
      "tipo": "VALOR",
      "unidad": "CUENTAS",
      "universo": "COHORTE",
      "version": "analytics-metrics/public-v1",
      "vistas": [
        "ACUMULADA"
      ]
    },
    {
      "aditiva": true,
      "agregacion": "SUMA",
      "cobertura_de_pagos": true,
      "codigo": "recuperacion.neta",
      "denominador": null,
      "descripcion": "La recuperacion economica de la cohorte en el horizonte, con la semantica de motor-pagos/v1 al final de as_of: los pagos vigentes mas los posibles reversos. Puede ser negativa.",
      "dimensiones_permitidas": [
        {
          "conjunto": "TOTAL",
          "llaves": []
        },
        {
          "conjunto": "PRODUCTO",
          "llaves": [
            "producto"
          ]
        },
        {
          "conjunto": "ATRASO_BUCKET",
          "llaves": [
            "atraso_bucket"
          ]
        },
        {
          "conjunto": "PRIORIDAD_DECISION",
          "llaves": [
            "prioridad_decision"
          ]
        },
        {
          "conjunto": "CANAL_DECISION",
          "llaves": [
            "canal_decision"
          ]
        },
        {
          "conjunto": "ENTIDAD",
          "llaves": [
            "entidad"
          ]
        },
        {
          "conjunto": "MUNICIPIO",
          "llaves": [
            "municipio"
          ]
        },
        {
          "conjunto": "BASE",
          "llaves": [
            "base"
          ]
        },
        {
          "conjunto": "PRODUCTO_X_ATRASO",
          "llaves": [
            "producto",
            "atraso_bucket"
          ]
        },
        {
          "conjunto": "BASE_X_PRIORIDAD",
          "llaves": [
            "base",
            "prioridad_decision"
          ]
        },
        {
          "conjunto": "CANAL_X_PRIORIDAD",
          "llaves": [
            "canal_decision",
            "prioridad_decision"
          ]
        }
      ],
      "familia": "RECUPERACION",
      "formula": "sum(PAGO no anulado) + sum(POSIBLE_REVERSO)",
      "fuentes_requeridas": [
        "CORTE",
        "MOTOR_PAGOS"
      ],
      "limitaciones": [
        "Reutiliza la interpretacion economica de motor-pagos/v1: un PAGO no anulado suma, un POSIBLE_REVERSO resta, y un REVERSO y el pago que anula suman cero juntos y no entran.",
        "No es contabilidad del acreedor: el saldo oficial sigue siendo el del snapshot.",
        "Puede ser negativa: un posible reverso en el horizonte resta, y no se acota a cero."
      ],
      "monetaria": true,
      "nombre": "Recuperacion economica neta",
      "nulo": "NO_DISPONIBLE si no se declaro ninguna interpretacion de pagos, o si algun dia del periodo no tiene cobertura de pagos conocida a known_at: ningun archivo de pagos aceptado lo cubre, o la interpretacion declarada de su mes no vio todos sus pagos observados. Nunca un cero inventado.",
      "numerador": null,
      "poblacion": "Los movimientos economicos canonicos de motor-pagos/v1 conciliados con una cuenta de la cohorte por su cuenta_canonica_id, nunca por el texto de CLIENTE_UNICO.",
      "puede_ser_negativa": true,
      "regla_temporal": "Los movimientos de las corridas de motor-pagos/v1 declaradas, recibidos (fecha_recepcion, hora local de la fuente) desde el inicio de fecha_corte hasta el final de as_of en la zona de la ejecucion; en PERIODO, en el dia, la semana ISO o el mes de su recepcion. Un PAGO esta anulado si el REVERSO que lo anula esta en una corrida declarada y se recibio antes del final de as_of: un reverso posterior no existe para este as_of.",
      "tipo": "VALOR",
      "unidad": "MONEDA",
      "universo": "COHORTE",
      "version": "analytics-metrics/public-v1",
      "vistas": [
        "ACUMULADA",
        "PERIODO"
      ]
    },
    {
      "aditiva": false,
      "agregacion": "RAZON",
      "cobertura_de_pagos": false,
      "codigo": "gestion.contactadas_sobre_gestionadas",
      "denominador": "cuentas distintas gestionadas",
      "descripcion": "De las cuentas gestionadas, que parte tuvo contacto: contactabilidad descriptiva, no una eficiencia.",
      "dimensiones_permitidas": [
        {
          "conjunto": "TOTAL",
          "llaves": []
        },
        {
          "conjunto": "PRODUCTO",
          "llaves": [
            "producto"
          ]
        },
        {
          "conjunto": "ATRASO_BUCKET",
          "llaves": [
            "atraso_bucket"
          ]
        },
        {
          "conjunto": "PRIORIDAD_DECISION",
          "llaves": [
            "prioridad_decision"
          ]
        },
        {
          "conjunto": "CANAL_DECISION",
          "llaves": [
            "canal_decision"
          ]
        },
        {
          "conjunto": "ENTIDAD",
          "llaves": [
            "entidad"
          ]
        },
        {
          "conjunto": "MUNICIPIO",
          "llaves": [
            "municipio"
          ]
        },
        {
          "conjunto": "BASE",
          "llaves": [
            "base"
          ]
        },
        {
          "conjunto": "PRODUCTO_X_ATRASO",
          "llaves": [
            "producto",
            "atraso_bucket"
          ]
        },
        {
          "conjunto": "BASE_X_PRIORIDAD",
          "llaves": [
            "base",
            "prioridad_decision"
          ]
        },
        {
          "conjunto": "CANAL_X_PRIORIDAD",
          "llaves": [
            "canal_decision",
            "prioridad_decision"
          ]
        },
        {
          "conjunto": "CANAL_GESTION",
          "llaves": [
            "canal_gestion"
          ]
        },
        {
          "conjunto": "MEDIO_GESTION",
          "llaves": [
            "medio_gestion"
          ]
        }
      ],
      "familia": "GESTION",
      "formula": "gestion.cuentas_contactadas / gestion.cuentas_gestionadas",
      "fuentes_requeridas": [
        "CORTE"
      ],
      "limitaciones": [
        "Contactada: con al menos una gestion efectiva con nivel_contacto CONTACTO_TITULAR o CONTACTO_TERCERO (lifecycle.reglas.CONTACTOS).",
        "Una razon literal, no una tasa de exito."
      ],
      "monetaria": false,
      "nombre": "Contactadas sobre gestionadas",
      "nulo": "Nunca NO_DISPONIBLE: el lifecycle es el registro de Motor Cartera, y 0 dice que no se conocia ninguna a known_at. Con denominador cero, valor NULL (estado DISPONIBLE, numerador y denominador 0).",
      "numerador": "cuentas distintas contactadas",
      "poblacion": "Las gestiones efectivas de las cuentas de la cohorte. Por CANAL_GESTION y MEDIO_GESTION, las cuentas con gestiones de ese canal o ese medio.",
      "puede_ser_negativa": false,
      "regla_temporal": "Gestiones efectivas: ocurridas desde el inicio de fecha_corte hasta el final de as_of, registradas hasta known_at y sin una anulacion que haya ocurrido antes del final de as_of y se haya registrado hasta known_at. En PERIODO, en el periodo de su ocurrido_en, en la zona de la ejecucion.",
      "tipo": "RAZON",
      "unidad": "RAZON",
      "universo": "COHORTE",
      "version": "analytics-metrics/public-v1",
      "vistas": [
        "ACUMULADA",
        "PERIODO"
      ]
    }
  ],
  "politicas": [
    {
      "conjuntos_en_periodo": [
        "TOTAL",
        "PRODUCTO",
        "ATRASO_BUCKET",
        "PRIORIDAD_DECISION",
        "CANAL_DECISION",
        "ENTIDAD",
        "BASE",
        "CANAL_GESTION",
        "MEDIO_GESTION"
      ],
      "escala_razon": 10,
      "hash": "3529b0893889bf8b28491c7fd2671cd3961b83915ba83084146585351fcb8a80",
      "max_periodos": 366,
      "min_group_size": 10,
      "reglas": [
        "horizonte",
        "relojes",
        "manifiesto",
        "anulacion",
        "reverso",
        "cobertura_pagos",
        "evaluacion",
        "plan",
        "supresion",
        "razon"
      ],
      "version": "analytics-public-v1"
    }
  ],
  "version": "analytics-metrics/public-v1",
  "version_motor": "analytics/v1"
};

export const ejecucionAnalytics: Respuesta<"/analytics/ejecuciones/{analytics_run_id}"> = {
  "agregados_publicados": 20331,
  "analytics_run_id": "f2c5b154-a3f3-4105-94a0-f14032d00e37",
  "as_of": "2026-10-01",
  "aviso": "analytics/v1 describe lo observado y lo planificado, agregado: no prueba causalidad, no predice y no recomienda. Una asociacion de la atribucion es operacional, no causal; una parada programada no es una visita realizada; un gestor de campo no es el actor_ref de una gestion. Las celdas con menos cuentas que el minimo de la politica se suprimen.",
  "controles_fallidos": 0,
  "controles_totales": 63,
  "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
  "cuentas_cohorte": 1000,
  "detalle": "Se analizo la cohorte de 1,000 cuentas del corte del 2026-09-30 hasta el 2026-10-01 (2 dias, DIA), con lo que se conocia hasta 2026-10-17T13:15:35.619443+00:00: 20,331 agregados (273 celdas suprimidas) y 63 controles, todos OK. Analytics describe lo observado y lo planificado, agregado: no prueba causalidad, no predice y no recomienda. Una asociacion de la atribucion es operacional, no causal; una parada programada no es una visita realizada; un gestor de campo no es el actor_ref de una gestion. analytics/v1.",
  "disponibilidad": {
    "celdas_suprimidas": 273,
    "cobertura_pagos": {
      "archivos": 4,
      "dias": 2,
      "dias_con_cobertura": 1,
      "meses": [
        {
          "atribuido": true,
          "clasificados": 541,
          "interpretado": true,
          "mes": "2026-09-01",
          "pagos_observados": 541
        },
        {
          "atribuido": false,
          "clasificados": 1740,
          "interpretado": true,
          "mes": "2026-10-01",
          "pagos_observados": 1740
        }
      ]
    },
    "conjuntos_no_disponibles": {
      "CAMPO": [
        "BASE",
        "BASE_X_PRIORIDAD",
        "ESTADO_CAMPO",
        "GESTOR_CAMPO",
        "PRIORIDAD_DECISION"
      ],
      "COHORTE": [
        "BASE",
        "BASE_X_PRIORIDAD"
      ],
      "GESTION": [],
      "RUTEO": [
        "BASE",
        "ESTADO_RUTEO",
        "GESTOR_CAMPO",
        "PRIORIDAD_DECISION"
      ]
    },
    "entradas": [
      "ATRIBUCION",
      "CORTE",
      "DECISION_V2",
      "EVALUACION_PROMESAS",
      "GEOGRAFIA",
      "MOTOR_PAGOS"
    ],
    "familias": {
      "ATRIBUCION": {
        "dias_sin_cobertura": 1,
        "estado": "PARCIAL"
      },
      "CALIDAD_PAGOS": {
        "dias_sin_cobertura": 1,
        "estado": "PARCIAL"
      },
      "CAMPO": {
        "estado": "NO_DISPONIBLE",
        "faltan": [
          "CAMPO_V2",
          "TERRITORIAL_V2"
        ]
      },
      "CARTERA": {
        "estado": "DISPONIBLE"
      },
      "DECISION": {
        "estado": "DISPONIBLE"
      },
      "GEOGRAFIA": {
        "estado": "DISPONIBLE"
      },
      "GESTION": {
        "estado": "DISPONIBLE"
      },
      "PROMESA": {
        "estado": "DISPONIBLE"
      },
      "RECUPERACION": {
        "dias_sin_cobertura": 1,
        "estado": "PARCIAL"
      },
      "RUTEO": {
        "estado": "NO_DISPONIBLE",
        "faltan": [
          "CAMPO_V2",
          "RUTEO_V2",
          "TERRITORIAL_V2"
        ]
      },
      "TERRITORIAL": {
        "estado": "NO_DISPONIBLE",
        "faltan": [
          "TERRITORIAL_V2"
        ]
      }
    },
    "periodos": 2
  },
  "duracion_segundos": 1.05815,
  "entradas": [
    {
      "disponible_en": "2026-10-17T13:14:28.468658Z",
      "firma": "fabd91d5960501480ba0e0118dc16f50927367346bb3ccad8a5859082fa2512d",
      "metadatos": {
        "movimientos_evaluados": 538,
        "ventana_dias": 30
      },
      "periodo_desde": "2026-09-01",
      "periodo_hasta": "2026-09-30",
      "referencia_publica": "9a51bd71-09fb-4202-976d-21956a565faa",
      "tipo": "ATRIBUCION",
      "version": "atribucion/v1"
    },
    {
      "disponible_en": "2026-10-17T13:14:16.920183Z",
      "firma": "a5c54444360efe7c1619339ec5cf6ceff19c1917ed38bccf39b042a587f2def5",
      "metadatos": {
        "cuentas": 1000
      },
      "periodo_desde": "2026-09-30",
      "periodo_hasta": "2026-09-30",
      "referencia_publica": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
      "tipo": "CORTE",
      "version": "historia/v1"
    },
    {
      "disponible_en": "2026-10-17T13:14:30.301800Z",
      "firma": "b30987e15bb8cec05f12bc4019393cd5386e53224566dd7fa8c24fa908e8036d",
      "metadatos": {
        "cuentas_decididas": 1000,
        "hash_politica": "2f9687400863f6af49a3c0fc51fe10c6c3b6f080804fcd4ef643ddad641d29e3",
        "version_politica": "decision-policy/public-v1"
      },
      "periodo_desde": "2026-09-30",
      "periodo_hasta": "2026-09-30",
      "referencia_publica": "13c166a9-218f-435d-a36c-5aea9eaee03e",
      "tipo": "DECISION_V2",
      "version": "decision/v2"
    },
    {
      "disponible_en": "2026-10-17T13:14:42.643089Z",
      "firma": "6e6f9d38c3977cd64cf4cbcc212327b8a7917b38dff37317932d6a02e7ba8552",
      "metadatos": {
        "promesas_evaluadas": 2
      },
      "periodo_desde": "2026-10-01",
      "periodo_hasta": "2026-10-01",
      "referencia_publica": "d40af884-dfcd-461c-9efb-b2d1cef1930d",
      "tipo": "EVALUACION_PROMESAS",
      "version": "evaluacion-promesa/v1"
    },
    {
      "disponible_en": "2026-10-17T13:14:31.620108Z",
      "firma": "46cbb941d61a65f1307fbaa3f88ca4883d5284bddadce9f7c5d7c26b559c1117",
      "metadatos": {
        "cuentas_objetivo": 1000
      },
      "periodo_desde": null,
      "periodo_hasta": null,
      "referencia_publica": "7455fff1-9fd1-4b8b-a217-cbfd1865f6e4",
      "tipo": "GEOGRAFIA",
      "version": "geografia/v1"
    },
    {
      "disponible_en": "2026-10-17T13:14:18.104918Z",
      "firma": "3543debcfbabae19390c9301f3131b8e3334aa71e288a171a209bb1738096425",
      "metadatos": {
        "movimientos_canonicos": 538,
        "observaciones_leidas": 541
      },
      "periodo_desde": "2026-09-01",
      "periodo_hasta": "2026-09-30",
      "referencia_publica": "99b8c5a6-c05b-44eb-8699-a23ed11d1e14",
      "tipo": "MOTOR_PAGOS",
      "version": "motor-pagos/v1"
    },
    {
      "disponible_en": "2026-10-17T13:14:24.654257Z",
      "firma": "fdbe56bbbeb3f21b246ea0a3b5ef1b7b7f32223d4b3ffc8bc2ce8c09fc1b41c1",
      "metadatos": {
        "movimientos_canonicos": 1731,
        "observaciones_leidas": 1740
      },
      "periodo_desde": "2026-10-01",
      "periodo_hasta": "2026-10-31",
      "referencia_publica": "9cc3041a-5c91-435f-9572-8e284a1a8358",
      "tipo": "MOTOR_PAGOS",
      "version": "motor-pagos/v1"
    }
  ],
  "estado": "EXITOSA",
  "fecha_corte": "2026-09-30",
  "firma_inputs": "394a082a72e220a33a0e0da2300d4358e90d37d071e8f487573a8db73b5fa12a",
  "granularidad": "DIA",
  "hash_agregados": "d31b0dc98a7196eaecc79095cc0e0c0e8f16d40eb6c3b7ffd82898b465a14385",
  "hash_catalogo": "553326f78c2ab17b48c8768af5e6dbb2c039f4b452aad092eb42b4113eacff64",
  "hash_manifiesto": "f23d06e2bf2ea3c9c1fa034aa4d20047246d0c258e56cdd5e15de84904148a0b",
  "hash_politica": "3529b0893889bf8b28491c7fd2671cd3961b83915ba83084146585351fcb8a80",
  "horizonte_dias": 2,
  "iniciada_en": "2026-10-17T13:15:41.444118Z",
  "known_at": "2026-10-17T13:15:35.619443Z",
  "modo": "OPERATIVO",
  "resultado": "ANALYTICS_PUBLICADA",
  "terminada_en": "2026-10-17T13:15:42.502268Z",
  "trabajo_id": "71cc3316-85cc-41f0-980b-61b0c8758e29",
  "version_catalogo": "analytics-metrics/public-v1",
  "version_motor": "analytics/v1",
  "version_politica": "analytics-public-v1",
  "zona_horaria": "America/Mexico_City"
};

export const serie: Respuesta<"/analytics/ejecuciones/{analytics_run_id}/agregados"> = {
  "analytics_run_id": "f2c5b154-a3f3-4105-94a0-f14032d00e37",
  "elementos": [
    {
      "denominador": null,
      "dimension_hash": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
      "dimension_set": "TOTAL",
      "dimensiones": {},
      "estado_valor": "DISPONIBLE",
      "metrica": "recuperacion.neta",
      "motivos": [],
      "numerador": null,
      "periodo_desde": "2026-09-30",
      "periodo_hasta": "2026-09-30",
      "periodo_parcial": false,
      "poblacion_cuentas": 1000,
      "valor": "194910.59",
      "vista": "PERIODO"
    },
    {
      "denominador": null,
      "dimension_hash": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
      "dimension_set": "TOTAL",
      "dimensiones": {},
      "estado_valor": "NO_DISPONIBLE",
      "metrica": "recuperacion.neta",
      "motivos": [
        {
          "codigo": "PAGOS_SIN_COBERTURA",
          "desde": "2026-10-01",
          "dias": 1,
          "hasta": "2026-10-01"
        }
      ],
      "numerador": null,
      "periodo_desde": "2026-10-01",
      "periodo_hasta": "2026-10-01",
      "periodo_parcial": false,
      "poblacion_cuentas": 1000,
      "valor": null,
      "vista": "PERIODO"
    }
  ],
  "pagina": 1,
  "por_pagina": 500,
  "total": 2
};

export const categorias: Respuesta<"/analytics/ejecuciones/{analytics_run_id}/agregados"> = {
  "analytics_run_id": "f2c5b154-a3f3-4105-94a0-f14032d00e37",
  "elementos": [
    {
      "denominador": null,
      "dimension_hash": "1bafa63549d0b70a8f5c2e8ceec4186c947cc3b52621afe2f5e17025837745ba",
      "dimension_set": "PRODUCTO",
      "dimensiones": {
        "producto": "CONSUMO"
      },
      "estado_valor": "NO_DISPONIBLE",
      "metrica": "recuperacion.neta",
      "motivos": [
        {
          "codigo": "PAGOS_SIN_COBERTURA",
          "desde": "2026-10-01",
          "dias": 1,
          "hasta": "2026-10-01"
        }
      ],
      "numerador": null,
      "periodo_desde": "2026-09-30",
      "periodo_hasta": "2026-10-01",
      "periodo_parcial": false,
      "poblacion_cuentas": 445,
      "valor": null,
      "vista": "ACUMULADA"
    },
    {
      "denominador": null,
      "dimension_hash": "3417aa6ff2c80a9afa682d33b61aa0985ef46908e07bec4b6843bf6d92ed0c1d",
      "dimension_set": "PRODUCTO",
      "dimensiones": {
        "producto": "NOMINA"
      },
      "estado_valor": "NO_DISPONIBLE",
      "metrica": "recuperacion.neta",
      "motivos": [
        {
          "codigo": "PAGOS_SIN_COBERTURA",
          "desde": "2026-10-01",
          "dias": 1,
          "hasta": "2026-10-01"
        }
      ],
      "numerador": null,
      "periodo_desde": "2026-09-30",
      "periodo_hasta": "2026-10-01",
      "periodo_parcial": false,
      "poblacion_cuentas": 181,
      "valor": null,
      "vista": "ACUMULADA"
    },
    {
      "denominador": null,
      "dimension_hash": "c52232ad726c533118a403d7b042f7f3c11b93b2128cac0b7d5646e5a8982c24",
      "dimension_set": "PRODUCTO",
      "dimensiones": {
        "producto": "AUTOMOTRIZ"
      },
      "estado_valor": "NO_DISPONIBLE",
      "metrica": "recuperacion.neta",
      "motivos": [
        {
          "codigo": "PAGOS_SIN_COBERTURA",
          "desde": "2026-10-01",
          "dias": 1,
          "hasta": "2026-10-01"
        }
      ],
      "numerador": null,
      "periodo_desde": "2026-09-30",
      "periodo_hasta": "2026-10-01",
      "periodo_parcial": false,
      "poblacion_cuentas": 93,
      "valor": null,
      "vista": "ACUMULADA"
    },
    {
      "denominador": null,
      "dimension_hash": "f292d1825f85a454d5f74fa2ba90b094b8fe61863a56d68555acc92f03e1f420",
      "dimension_set": "PRODUCTO",
      "dimensiones": {
        "producto": "TARJETA"
      },
      "estado_valor": "NO_DISPONIBLE",
      "metrica": "recuperacion.neta",
      "motivos": [
        {
          "codigo": "PAGOS_SIN_COBERTURA",
          "desde": "2026-10-01",
          "dias": 1,
          "hasta": "2026-10-01"
        }
      ],
      "numerador": null,
      "periodo_desde": "2026-09-30",
      "periodo_hasta": "2026-10-01",
      "periodo_parcial": false,
      "poblacion_cuentas": 281,
      "valor": null,
      "vista": "ACUMULADA"
    }
  ],
  "pagina": 1,
  "por_pagina": 100,
  "total": 4
};

export const embudos: Respuesta<"/analytics/ejecuciones/{analytics_run_id}/embudos"> = {
  "analytics_run_id": "f2c5b154-a3f3-4105-94a0-f14032d00e37",
  "aviso": "analytics/v1 describe lo observado y lo planificado, agregado: no prueba causalidad, no predice y no recomienda. Una asociacion de la atribucion es operacional, no causal; una parada programada no es una visita realizada; un gestor de campo no es el actor_ref de una gestion. Las celdas con menos cuentas que el minimo de la politica se suprimen.",
  "embudos": [
    {
      "embudo": "CONTACTO",
      "etapas": [
        {
          "cuentas": 1000,
          "estado_valor": "DISPONIBLE",
          "etapa": "COHORTE",
          "metrica": "cartera.cuentas",
          "pct_cohorte": "1.0000000000",
          "pct_etapa_anterior": null
        },
        {
          "cuentas": 2,
          "estado_valor": "DISPONIBLE",
          "etapa": "GESTIONADAS",
          "metrica": "gestion.cuentas_gestionadas",
          "pct_cohorte": "0.0020000000",
          "pct_etapa_anterior": "0.0020000000"
        },
        {
          "cuentas": 1,
          "estado_valor": "DISPONIBLE",
          "etapa": "CONTACTADAS",
          "metrica": "gestion.cuentas_contactadas",
          "pct_cohorte": "0.0010000000",
          "pct_etapa_anterior": "0.5000000000"
        },
        {
          "cuentas": 1,
          "estado_valor": "DISPONIBLE",
          "etapa": "CON_PROMESA",
          "metrica": "promesa.cuentas_con_promesa",
          "pct_cohorte": "0.0010000000",
          "pct_etapa_anterior": "1.0000000000"
        }
      ]
    },
    {
      "embudo": "CAMPO",
      "etapas": [
        {
          "cuentas": null,
          "estado_valor": "NO_DISPONIBLE",
          "etapa": "CANDIDATAS",
          "metrica": "campo.candidatas",
          "pct_cohorte": null,
          "pct_etapa_anterior": null
        },
        {
          "cuentas": null,
          "estado_valor": "NO_DISPONIBLE",
          "etapa": "ASIGNADAS",
          "metrica": "campo.asignadas",
          "pct_cohorte": null,
          "pct_etapa_anterior": null
        },
        {
          "cuentas": null,
          "estado_valor": "NO_DISPONIBLE",
          "etapa": "PARADAS_PROGRAMADAS",
          "metrica": "ruteo.programadas",
          "pct_cohorte": null,
          "pct_etapa_anterior": null
        }
      ]
    }
  ]
};

export const controles: Respuesta<"/analytics/ejecuciones/{analytics_run_id}/controles"> = {
  "analytics_run_id": "f2c5b154-a3f3-4105-94a0-f14032d00e37",
  "controles": [
    {
      "codigo": "atribucion.clases_suman_evaluados",
      "detalle": "ASOCIACION_UNICA + AMBIGUA + SIN_GESTION_CANDIDATA contra los evaluados",
      "diferencia": "0",
      "esperado": "83",
      "estado": "OK",
      "observado": "83"
    },
    {
      "codigo": "atribucion.cubre_los_pagos",
      "detalle": "los PAGO de la cohorte de los meses con atribucion declarada contra los evaluados",
      "diferencia": "0",
      "esperado": "83",
      "estado": "OK",
      "observado": "83"
    },
    {
      "codigo": "atribucion.montos_suman_pagos_vigentes",
      "detalle": "los montos de las tres clases contra los PAGO vigentes de esos meses, en centavos",
      "diferencia": "0.00",
      "esperado": "194910.59",
      "estado": "OK",
      "observado": "194910.59"
    },
    {
      "codigo": "cartera.cuentas.por_atraso_bucket",
      "detalle": "sum(cuentas) de las celdas de ATRASO_BUCKET contra TOTAL, en COHORTE",
      "diferencia": "0",
      "esperado": "1000",
      "estado": "OK",
      "observado": "1000"
    },
    {
      "codigo": "cartera.cuentas.por_canal_decision",
      "detalle": "sum(cuentas) de las celdas de CANAL_DECISION contra TOTAL, en COHORTE",
      "diferencia": "0",
      "esperado": "1000",
      "estado": "OK",
      "observado": "1000"
    },
    {
      "codigo": "cartera.cuentas.por_canal_x_prioridad",
      "detalle": "sum(cuentas) de las celdas de CANAL_X_PRIORIDAD contra TOTAL, en COHORTE",
      "diferencia": "0",
      "esperado": "1000",
      "estado": "OK",
      "observado": "1000"
    },
    {
      "codigo": "cartera.cuentas.por_entidad",
      "detalle": "sum(cuentas) de las celdas de ENTIDAD contra TOTAL, en COHORTE",
      "diferencia": "0",
      "esperado": "1000",
      "estado": "OK",
      "observado": "1000"
    },
    {
      "codigo": "cartera.cuentas.por_municipio",
      "detalle": "sum(cuentas) de las celdas de MUNICIPIO contra TOTAL, en COHORTE",
      "diferencia": "0",
      "esperado": "1000",
      "estado": "OK",
      "observado": "1000"
    },
    {
      "codigo": "cartera.cuentas.por_prioridad_decision",
      "detalle": "sum(cuentas) de las celdas de PRIORIDAD_DECISION contra TOTAL, en COHORTE",
      "diferencia": "0",
      "esperado": "1000",
      "estado": "OK",
      "observado": "1000"
    },
    {
      "codigo": "cartera.cuentas.por_producto",
      "detalle": "sum(cuentas) de las celdas de PRODUCTO contra TOTAL, en COHORTE",
      "diferencia": "0",
      "esperado": "1000",
      "estado": "OK",
      "observado": "1000"
    },
    {
      "codigo": "cartera.cuentas.por_producto_x_atraso",
      "detalle": "sum(cuentas) de las celdas de PRODUCTO_X_ATRASO contra TOTAL, en COHORTE",
      "diferencia": "0",
      "esperado": "1000",
      "estado": "OK",
      "observado": "1000"
    },
    {
      "codigo": "cartera.saldo_total.por_atraso_bucket",
      "detalle": "sum(saldo_total) de las celdas de ATRASO_BUCKET contra TOTAL, en COHORTE",
      "diferencia": "0.00",
      "esperado": "37691314.06",
      "estado": "OK",
      "observado": "37691314.06"
    }
  ],
  "controles_fallidos": 0,
  "controles_totales": 63,
  "estado": "EXITOSA",
  "resultado": "ANALYTICS_PUBLICADA"
};

export const scoring: Respuesta<"/predictivo/scorings/{scoring_run_id}"> = {
  "as_of": "2026-09-30",
  "aviso": "predictive/model-v1 estima la probabilidad de resultados observados bajo la operacion historica, con lo que se conocia a known_at. No es un efecto causal, no recomienda una accion y no cambia decision/v2, campo/v2 ni ruteo/v2: corre en paralelo, en sombra.",
  "cartera_id": "CARTERA_PRINCIPAL",
  "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
  "cuentas_objetivo": 1000,
  "despacho_id": "DSP_001",
  "detalle": "Se publicaron 1,000 predicciones del corte del 2026-09-30 con lo que se conocia a 2026-10-17T13:14:29.444442+00:00. El modelo se entreno con DSP_001/PREDICTIVO: su drift dice cuanto se parece esta cartera. Una prediccion es la probabilidad de un resultado observado bajo la operacion historica, con lo que se conocia a known_at: no es el efecto de ninguna accion ni una recomendacion, y no cambia decision/v2, campo/v2 ni ruteo/v2.",
  "disponibilidad": {
    "dias_sin_cobertura_90d": 83,
    "evaluacion_promesas": {
      "as_of": "2026-09-30",
      "evaluacion_run_id": "f4ca4fe9-a9c3-4a64-b767-61bdfb18f75b"
    },
    "meses": [
      {
        "clasificados": 541,
        "interpretado": true,
        "mes": "2026-09-01",
        "pagos_observados": 541
      }
    ],
    "pagos": {
      "30d": false,
      "90d": false
    }
  },
  "drift_resumen": {
    "aviso": "El drift describe cuanto se parece lo puntuado a TRAIN. No cambia ni reentrena el modelo; sus umbrales son heuristicas operacionales.",
    "cartera_distinta": true,
    "faltantes": {
      "ALTO": 6,
      "BAJO": 28,
      "MODERADO": 3
    },
    "feature": {
      "ALTO": 16,
      "BAJO": 22,
      "MODERADO": 2
    },
    "mayor_psi": [
      {
        "nombre": "delta_dias_atraso_1_corte",
        "psi": 8.823712,
        "severidad": "ALTO",
        "tipo": "FEATURE"
      },
      {
        "nombre": "tendencia_atraso",
        "psi": 8.567815,
        "severidad": "ALTO",
        "tipo": "FEATURE"
      },
      {
        "nombre": "dias_desde_ultimo_contacto",
        "psi": 8.525797,
        "severidad": "ALTO",
        "tipo": "FEATURE"
      },
      {
        "nombre": "delta_dias_atraso_4_semanas",
        "psi": 8.254702,
        "severidad": "ALTO",
        "tipo": "FEATURE"
      },
      {
        "nombre": "dias_desde_ultima_promesa",
        "psi": 7.971063,
        "severidad": "ALTO",
        "tipo": "FEATURE"
      }
    ],
    "score": {
      "ALTO": 2,
      "BAJO": 1,
      "MODERADO": 1
    }
  },
  "duracion_segundos": 0.626436,
  "estado": "EXITOSA",
  "firma_inputs": "7cff352311bff21c8da67f7c8df64ee81ac8182fad3715de4322a1e848a6a3dd",
  "hash_drift": "23a18a1d19bbf63dad6df52f7ec937421c1a8641e177dd5687144b83a7e2e2a8",
  "hash_features": "786b5ef4b5a21a7583f59082196f77bc4900636ed1089e749fe3a726f9db5e94",
  "hash_predicciones": "eb56caad0e6dc9af93e7f6b72508ef5af527e61fb153e34cec9e262e73f268bc",
  "iniciada_en": "2026-10-17T13:16:43.369953Z",
  "known_at": "2026-10-17T13:14:29.444442Z",
  "modelo_version_id": "261a485b-7f86-44f9-808a-190f164fd471",
  "modo": "BACKTEST",
  "predicciones_publicadas": 1000,
  "resultado": "PREDICCIONES_PUBLICADAS",
  "scoring_run_id": "c1397868-406c-431b-ae0a-25a791e574ae",
  "terminada_en": "2026-10-17T13:16:43.996389Z",
  "tiempos": {
    "fases_segundos": {
      "conversion": 0.003,
      "drift": 0.003,
      "features": 0.174,
      "hashes": 0.011,
      "inferencia": 0.011,
      "publicacion": 0.039,
      "staging": 0.013
    },
    "memoria_pico_mb": 275.9,
    "total_segundos": 0.256
  },
  "trabajo_id": "a439cafa-4b80-451b-a879-471f85f86092",
  "version_features": "predictive-features/public-v1",
  "zona_horaria": "America/Mexico_City"
};

export const modelo: Respuesta<"/predictivo/modelos/{modelo_version_id}"> = {
  "aviso": "predictive/model-v1 estima la probabilidad de resultados observados bajo la operacion historica, con lo que se conocia a known_at. No es un efecto causal, no recomienda una accion y no cambia decision/v2, campo/v2 ni ruteo/v2: corre en paralelo, en sombra.",
  "componentes": [
    {
      "algoritmo": "LOGISTICA_L2",
      "artefacto": {
        "algoritmo": "LOGISTICA_L2",
        "calibracion": {
          "a": -0.358522856918,
          "b": 0.873227640584,
          "metodo": "PLATT"
        },
        "coeficientes": [
          -0.137694793369,
          -0.0908337775055,
          0.157005295869,
          0.157005295869
        ],
        "columnas": [
          "saldo_total",
          "saldo",
          "saldo_atrasado",
          "saldo_requerido"
        ],
        "formato": "predictive/artifact-v1",
        "hash_features": "786b5ef4b5a21a7583f59082196f77bc4900636ed1089e749fe3a726f9db5e94",
        "hiperparametros": {
          "c": 0.1,
          "penalizacion": "L2",
          "rebalanceo": null
        },
        "intercepto": -0.604062104982,
        "liga": "logit",
        "precision": {
          "cifras_significativas": 12,
          "decimales_monto": 2,
          "decimales_probabilidad": 8
        },
        "preprocesamiento": {
          "categoricas": [
            {
              "constantes": [
                false,
                false,
                false,
                false
              ],
              "nombre": "producto",
              "vocabulario": [
                "AUTOMOTRIZ",
                "CONSUMO",
                "NOMINA",
                "TARJETA"
              ]
            },
            {
              "constantes": [
                false,
                false,
                false,
                false
              ],
              "nombre": "cve_entidad",
              "vocabulario": [
                "09",
                "15",
                "21",
                "29"
              ]
            },
            {
              "constantes": [
                false,
                false,
                false,
                false
              ],
              "nombre": "tramo_atraso",
              "vocabulario": [
                "0",
                "1-30",
                "31-60",
                "61-90"
              ]
            }
          ],
          "numericas": [
            {
              "constante": false,
              "desviacion": 1.16525514499,
              "indicador_constante": true,
              "indicador_nulo": false,
              "media": 9.86479019196,
              "mediana": 9.80937795175,
              "nombre": "saldo_total",
              "transformacion": "LOG1P"
            },
            {
              "constante": false,
              "desviacion": 1.15513723332,
              "indicador_constante": true,
              "indicador_nulo": true,
              "media": 9.7131841037,
              "mediana": 9.63602958717,
              "nombre": "saldo",
              "transformacion": "LOG1P"
            },
            {
              "constante": false,
              "desviacion": 3.07013889767,
              "indicador_constante": true,
              "indicador_nulo": true,
              "media": 7.04188944924,
              "mediana": 7.62123435851,
              "nombre": "saldo_atrasado",
              "transformacion": "LOG1P"
            },
            {
              "constante": false,
              "desviacion": 3.07013889767,
              "indicador_constante": true,
              "indicador_nulo": true,
              "media": 7.04188944924,
              "mediana": 7.62123435851,
              "nombre": "saldo_requerido",
              "transformacion": "LOG1P"
            }
          ]
        },
        "target": "CONTACTO_14D",
        "version_features": "predictive-features/public-v1"
      },
      "baseline_test": {
        "brier": 0.23131859354391116,
        "calibracion_intercepto": 0.1138460605017811,
        "calibracion_pendiente": 0.0,
        "captura_10": 0.09166666666666666,
        "captura_20": 0.2,
        "ece": 0.043254385554452796,
        "lift_10": 0.9098258706467662,
        "lift_20": 1.0,
        "log_loss": 0.6554139033459765,
        "n": 665,
        "pr_auc": 0.3609022556390977,
        "prediccion": 0.3350802489354733,
        "prevalencia": 0.3609022556390977,
        "roc_auc": 0.5
      },
      "bootstrap_test": {
        "cuentas": 665,
        "intervalos": {
          "brier": {
            "estimado": 0.21853297510101208,
            "inferior": 0.20573836102871831,
            "superior": 0.23128500295951368
          },
          "diferencia_brier": {
            "estimado": -0.012785618442899077,
            "inferior": -0.02204870134748753,
            "superior": -0.0032898264275081314
          },
          "diferencia_log_loss": {
            "estimado": -0.025831235577629008,
            "inferior": -0.04588318555182273,
            "superior": -0.005215745602491297
          },
          "log_loss": {
            "estimado": 0.6295826677683475,
            "inferior": 0.5995879513569738,
            "superior": 0.6575761558968993
          },
          "pr_auc": {
            "estimado": 0.4994678434118984,
            "inferior": 0.4392650559156518,
            "superior": 0.5706728165752974
          },
          "roc_auc": {
            "estimado": 0.637,
            "inferior": 0.5919208603095217,
            "superior": 0.6793776501535583
          }
        },
        "nivel": 0.95,
        "repeticiones": 200,
        "semilla": 2026,
        "unidad": "cuenta"
      },
      "calibracion": "PLATT",
      "compuertas": [
        {
          "compuerta": "LOG_LOSS_NO_PEOR_QUE_BASELINE",
          "ok": true,
          "operador": "<=",
          "umbral": 0.6554139033459765,
          "valor": 0.6295826677683475
        },
        {
          "compuerta": "BRIER_NO_PEOR_QUE_BASELINE",
          "ok": true,
          "operador": "<=",
          "umbral": 0.23131859354391116,
          "valor": 0.21853297510101208
        },
        {
          "compuerta": "ROC_AUC_MAYOR_QUE_0_5",
          "ok": true,
          "operador": ">",
          "umbral": 0.5,
          "valor": 0.637
        },
        {
          "compuerta": "PROBABILIDADES_VALIDAS",
          "ok": true,
          "operador": "==",
          "umbral": true,
          "valor": true
        }
      ],
      "estado": "VALIDADO",
      "explicacion_global": [
        {
          "coeficiente": 0.312730367989,
          "columna": "tramo_atraso=61-90",
          "direccion": "+",
          "feature": "tramo_atraso",
          "magnitud": 0.312730367989
        },
        {
          "coeficiente": -0.243622814622,
          "columna": "tramo_atraso=1-30",
          "direccion": "-",
          "feature": "tramo_atraso",
          "magnitud": 0.243622814622
        },
        {
          "coeficiente": -0.231177468393,
          "columna": "tramo_atraso=0",
          "direccion": "-",
          "feature": "tramo_atraso",
          "magnitud": 0.231177468393
        },
        {
          "coeficiente": -0.211574101587,
          "columna": "gestiones_sin_contacto_30d",
          "direccion": "-",
          "feature": "gestiones_sin_contacto_30d",
          "magnitud": 0.211574101587
        }
      ],
      "hash_artefacto": "83e6390de8d3c078bce0f5bdd4d60760497c7960a22f6e5217f33bee4531f4bf",
      "hiperparametros": {
        "c": 0.1,
        "penalizacion": "L2",
        "rebalanceo": null
      },
      "metricas_test": {
        "brier": 0.21853297510101208,
        "calibracion_intercepto": 0.036250607821987,
        "calibracion_pendiente": 0.8117477751737882,
        "captura_10": 0.15416666666666667,
        "captura_20": 0.3,
        "ece": 0.049791309338345864,
        "lift_10": 1.5301616915422886,
        "lift_20": 1.5,
        "log_loss": 0.6295826677683475,
        "n": 665,
        "pr_auc": 0.4994678434118984,
        "prevalencia": 0.3609022556390977,
        "roc_auc": 0.637,
        "sin_calibrar": {
          "brier": 0.22411376878692435,
          "calibracion_intercepto": -0.2920512677966972,
          "calibracion_pendiente": 0.7088405946120295,
          "captura_10": 0.15416666666666667,
          "captura_20": 0.3,
          "ece": 0.08247438129323308,
          "lift_10": 1.5301616915422886,
          "lift_20": 1.5,
          "log_loss": 0.6411955556870653,
          "n": 665,
          "pr_auc": 0.4994678434118984,
          "prevalencia": 0.3609022556390977,
          "roc_auc": 0.637
        },
        "tabla_calibracion": [
          {
            "bin": 1,
            "frecuencia_observada": 0.2835820895522388,
            "n": 67,
            "prediccion_maxima": 0.19615553,
            "prediccion_media": 0.16705844805970146,
            "prediccion_minima": 0.10225649
          },
          {
            "bin": 2,
            "frecuencia_observada": 0.14925373134328357,
            "n": 67,
            "prediccion_maxima": 0.23420679,
            "prediccion_media": 0.21434066462686568,
            "prediccion_minima": 0.19657637
          },
          {
            "bin": 3,
            "frecuencia_observada": 0.31343283582089554,
            "n": 67,
            "prediccion_maxima": 0.27456663,
            "prediccion_media": 0.2548292829850747,
            "prediccion_minima": 0.23457645
          },
          {
            "bin": 4,
            "frecuencia_observada": 0.26865671641791045,
            "n": 67,
            "prediccion_maxima": 0.30483566,
            "prediccion_media": 0.2903529465671641,
            "prediccion_minima": 0.27480356
          }
        ]
      },
      "metricas_validation": {
        "calibracion": {
          "brier_validation_platt": 0.2083798134126694,
          "brier_validation_raw": 0.21328722856119572,
          "elegida": "PLATT",
          "mejora_minima": 0.001,
          "platt": {
            "a": -0.358522856918,
            "b": 0.873227640584
          }
        },
        "elegido": {
          "c": 0.1,
          "penalizacion": "L2",
          "rebalanceo": null
        },
        "metricas": {
          "brier": 0.20837981350904136,
          "calibracion_intercepto": -3.323944548115993e-07,
          "calibracion_pendiente": 0.9999999968092828,
          "captura_10": 0.1583710407239819,
          "captura_20": 0.28054298642533937,
          "ece": 0.04223645489361702,
          "lift_10": 1.5789112847936377,
          "lift_20": 1.398464280817222,
          "log_loss": 0.6049005252862427,
          "n": 658,
          "pr_auc": 0.47047868791268094,
          "prevalencia": 0.33586626139817627,
          "roc_auc": 0.661948497054164
        },
        "rejilla": [
          {
            "brier": 0.21328722856119572,
            "c": 0.1,
            "convergio": true,
            "iteraciones": 4,
            "log_loss": 0.6161989994345548,
            "pr_auc": 0.47047868791268094,
            "roc_auc": 0.661948497054164
          },
          {
            "brier": 0.21466331270722236,
            "c": 1.0,
            "convergio": true,
            "iteraciones": 4,
            "log_loss": 0.6188213308108383,
            "pr_auc": 0.4745738551472685,
            "roc_auc": 0.6597533574246456
          },
          {
            "brier": 0.2159501972414483,
            "c": 10.0,
            "convergio": true,
            "iteraciones": 4,
            "log_loss": 0.6214377073819861,
            "pr_auc": 0.47121485994183315,
            "roc_auc": 0.6578274330327096
          }
        ]
      },
      "target": "CONTACTO_14D"
    },
    {
      "algoritmo": "LOGISTICA_L2",
      "artefacto": {
        "algoritmo": "LOGISTICA_L2",
        "calibracion": {
          "a": -0.892213181544,
          "b": 0.281123294643,
          "metodo": "PLATT"
        },
        "coeficientes": [
          -0.0509132816127,
          -0.0572758414122,
          0.0853721959946,
          0.0853721959946
        ],
        "columnas": [
          "saldo_total",
          "saldo",
          "saldo_atrasado",
          "saldo_requerido"
        ],
        "formato": "predictive/artifact-v1",
        "hash_features": "786b5ef4b5a21a7583f59082196f77bc4900636ed1089e749fe3a726f9db5e94",
        "hiperparametros": {
          "c": 0.1,
          "penalizacion": "L2",
          "rebalanceo": null
        },
        "intercepto": -2.10252115009,
        "liga": "logit",
        "precision": {
          "cifras_significativas": 12,
          "decimales_monto": 2,
          "decimales_probabilidad": 8
        },
        "preprocesamiento": {
          "categoricas": [
            {
              "constantes": [
                false,
                false,
                false,
                false
              ],
              "nombre": "producto",
              "vocabulario": [
                "AUTOMOTRIZ",
                "CONSUMO",
                "NOMINA",
                "TARJETA"
              ]
            },
            {
              "constantes": [
                false,
                false,
                false,
                false
              ],
              "nombre": "cve_entidad",
              "vocabulario": [
                "09",
                "15",
                "21",
                "29"
              ]
            },
            {
              "constantes": [
                false,
                false,
                false,
                false
              ],
              "nombre": "tramo_atraso",
              "vocabulario": [
                "0",
                "1-30",
                "31-60",
                "61-90"
              ]
            }
          ],
          "numericas": [
            {
              "constante": false,
              "desviacion": 1.16525514499,
              "indicador_constante": true,
              "indicador_nulo": false,
              "media": 9.86479019196,
              "mediana": 9.80937795175,
              "nombre": "saldo_total",
              "transformacion": "LOG1P"
            },
            {
              "constante": false,
              "desviacion": 1.15513723332,
              "indicador_constante": true,
              "indicador_nulo": true,
              "media": 9.7131841037,
              "mediana": 9.63602958717,
              "nombre": "saldo",
              "transformacion": "LOG1P"
            },
            {
              "constante": false,
              "desviacion": 3.07013889767,
              "indicador_constante": true,
              "indicador_nulo": true,
              "media": 7.04188944924,
              "mediana": 7.62123435851,
              "nombre": "saldo_atrasado",
              "transformacion": "LOG1P"
            },
            {
              "constante": false,
              "desviacion": 3.07013889767,
              "indicador_constante": true,
              "indicador_nulo": true,
              "media": 7.04188944924,
              "mediana": 7.62123435851,
              "nombre": "saldo_requerido",
              "transformacion": "LOG1P"
            }
          ]
        },
        "target": "PROMESA_30D",
        "version_features": "predictive-features/public-v1"
      },
      "baseline_test": {
        "brier": 0.14465342018615338,
        "calibracion_intercepto": 0.18769152787642845,
        "calibracion_pendiente": 0.0,
        "captura_10": 0.10344827586206896,
        "captura_20": 0.1896551724137931,
        "ece": 0.030924839120401733,
        "lift_10": 1.026762738033968,
        "lift_20": 0.9482758620689654,
        "log_loss": 0.4652853132192698,
        "n": 665,
        "pr_auc": 0.17443609022556392,
        "prediccion": 0.14903373730756633,
        "prevalencia": 0.17443609022556392,
        "roc_auc": 0.5
      },
      "bootstrap_test": {
        "cuentas": 665,
        "intervalos": {
          "brier": {
            "estimado": 0.14130877843627376,
            "inferior": 0.12188136499664917,
            "superior": 0.16178461396739405
          },
          "diferencia_brier": {
            "estimado": -0.003344641749879612,
            "inferior": -0.0058353421638473795,
            "superior": -0.000774956545420204
          },
          "diferencia_log_loss": {
            "estimado": -0.011320000353552573,
            "inferior": -0.020104049857522305,
            "superior": -0.002592849880145123
          },
          "log_loss": {
            "estimado": 0.45396531286571723,
            "inferior": 0.4069015008711052,
            "superior": 0.5046273773003302
          },
          "pr_auc": {
            "estimado": 0.25137795578948186,
            "inferior": 0.18960545275947893,
            "superior": 0.3241381219717437
          },
          "roc_auc": {
            "estimado": 0.6180359273914955,
            "inferior": 0.5638366830765213,
            "superior": 0.6760620230117339
          }
        },
        "nivel": 0.95,
        "repeticiones": 200,
        "semilla": 2026,
        "unidad": "cuenta"
      },
      "calibracion": "PLATT",
      "compuertas": [
        {
          "compuerta": "LOG_LOSS_NO_PEOR_QUE_BASELINE",
          "ok": true,
          "operador": "<=",
          "umbral": 0.4652853132192698,
          "valor": 0.45396531286571723
        },
        {
          "compuerta": "BRIER_NO_PEOR_QUE_BASELINE",
          "ok": true,
          "operador": "<=",
          "umbral": 0.14465342018615338,
          "valor": 0.14130877843627376
        },
        {
          "compuerta": "ROC_AUC_MAYOR_QUE_0_5",
          "ok": true,
          "operador": ">",
          "umbral": 0.5,
          "valor": 0.6180359273914955
        },
        {
          "compuerta": "PROBABILIDADES_VALIDAS",
          "ok": true,
          "operador": "==",
          "umbral": true,
          "valor": true
        }
      ],
      "estado": "VALIDADO",
      "explicacion_global": [
        {
          "coeficiente": 0.394619653045,
          "columna": "tramo_atraso=91+",
          "direccion": "+",
          "feature": "tramo_atraso",
          "magnitud": 0.394619653045
        },
        {
          "coeficiente": -0.388937860008,
          "columna": "atraso_maximo",
          "direccion": "-",
          "feature": "atraso_maximo",
          "magnitud": 0.388937860008
        },
        {
          "coeficiente": -0.258165782311,
          "columna": "tramo_atraso=1-30",
          "direccion": "-",
          "feature": "tramo_atraso",
          "magnitud": 0.258165782311
        },
        {
          "coeficiente": 0.217270880119,
          "columna": "dias_desde_ultimo_contacto__nulo",
          "direccion": "+",
          "feature": "dias_desde_ultimo_contacto",
          "magnitud": 0.217270880119
        }
      ],
      "hash_artefacto": "1514a69728b3c016cbb020f8458294493915bfc008d21d871ff087e5171cd680",
      "hiperparametros": {
        "c": 0.1,
        "penalizacion": "L2",
        "rebalanceo": null
      },
      "metricas_test": {
        "brier": 0.14130877843627376,
        "calibracion_intercepto": 0.12115089367977225,
        "calibracion_pendiente": 1.3365256685438984,
        "captura_10": 0.14655172413793102,
        "captura_20": 0.29310344827586204,
        "ece": 0.027528624781954888,
        "lift_10": 1.4545805455481216,
        "lift_20": 1.4655172413793103,
        "log_loss": 0.45396531286571723,
        "n": 665,
        "pr_auc": 0.25137795578948186,
        "prevalencia": 0.17443609022556392,
        "roc_auc": 0.6180359273914955,
        "sin_calibrar": {
          "brier": 0.15054465160657027,
          "calibracion_intercepto": 1.020697151226552,
          "calibracion_pendiente": 0.37572849487225707,
          "captura_10": 0.14655172413793102,
          "captura_20": 0.29310344827586204,
          "ece": 0.09769983556390978,
          "lift_10": 1.4545805455481216,
          "lift_20": 1.4655172413793103,
          "log_loss": 0.5324668869207071,
          "n": 665,
          "pr_auc": 0.25137795578948186,
          "prevalencia": 0.17443609022556392,
          "roc_auc": 0.6180359273914955
        },
        "tabla_calibracion": [
          {
            "bin": 1,
            "frecuencia_observada": 0.1044776119402985,
            "n": 67,
            "prediccion_maxima": 0.11422556,
            "prediccion_media": 0.10192718507462686,
            "prediccion_minima": 0.05691751
          },
          {
            "bin": 2,
            "frecuencia_observada": 0.07462686567164178,
            "n": 67,
            "prediccion_maxima": 0.12574911,
            "prediccion_media": 0.1200508728358209,
            "prediccion_minima": 0.11441046
          },
          {
            "bin": 3,
            "frecuencia_observada": 0.14925373134328357,
            "n": 67,
            "prediccion_maxima": 0.13474069,
            "prediccion_media": 0.13034337223880593,
            "prediccion_minima": 0.12580713
          },
          {
            "bin": 4,
            "frecuencia_observada": 0.13432835820895522,
            "n": 67,
            "prediccion_maxima": 0.14260787,
            "prediccion_media": 0.13852739313432838,
            "prediccion_minima": 0.13485131
          }
        ]
      },
      "metricas_validation": {
        "calibracion": {
          "brier_validation_platt": 0.1419280981477461,
          "brier_validation_raw": 0.14892529133948065,
          "elegida": "PLATT",
          "mejora_minima": 0.001,
          "platt": {
            "a": -0.892213181544,
            "b": 0.281123294643
          }
        },
        "elegido": {
          "c": 0.1,
          "penalizacion": "L2",
          "rebalanceo": null
        },
        "metricas": {
          "brier": 0.14192809820176813,
          "calibracion_intercepto": -3.1023852484797916e-09,
          "calibracion_pendiente": 0.9999999847027031,
          "captura_10": 0.17543859649122806,
          "captura_20": 0.2631578947368421,
          "ece": 0.034917778708206684,
          "lift_10": 1.749069643806486,
          "lift_20": 1.3118022328548644,
          "log_loss": 0.45700945256401115,
          "n": 658,
          "pr_auc": 0.23208038487286164,
          "prevalencia": 0.17325227963525835,
          "roc_auc": 0.5637093653250774
        },
        "rejilla": [
          {
            "brier": 0.14892529133948065,
            "c": 0.1,
            "convergio": true,
            "iteraciones": 5,
            "log_loss": 0.5083452663831401,
            "pr_auc": 0.23208038487286164,
            "roc_auc": 0.5637093653250774
          },
          {
            "brier": 0.15144852895694713,
            "c": 1.0,
            "convergio": true,
            "iteraciones": 5,
            "log_loss": 0.5317833354093487,
            "pr_auc": 0.22054614318405938,
            "roc_auc": 0.5464557533539731
          },
          {
            "brier": 0.15224187204706993,
            "c": 10.0,
            "convergio": true,
            "iteraciones": 5,
            "log_loss": 0.5397051835629507,
            "pr_auc": 0.2155273175805903,
            "roc_auc": 0.5396188080495357
          }
        ]
      },
      "target": "PROMESA_30D"
    },
    {
      "algoritmo": "LOGISTICA_L2",
      "artefacto": {
        "algoritmo": "LOGISTICA_L2",
        "calibracion": {
          "a": 0.479601382056,
          "b": 0.993346745738,
          "metodo": "PLATT"
        },
        "coeficientes": [
          0.0405127403138,
          0.149283624436,
          -0.0912470077207,
          -0.0912470077207
        ],
        "columnas": [
          "saldo_total",
          "saldo",
          "saldo_atrasado",
          "saldo_requerido"
        ],
        "formato": "predictive/artifact-v1",
        "hash_features": "786b5ef4b5a21a7583f59082196f77bc4900636ed1089e749fe3a726f9db5e94",
        "hiperparametros": {
          "c": 0.1,
          "penalizacion": "L2",
          "rebalanceo": null
        },
        "intercepto": -0.879408930251,
        "liga": "logit",
        "precision": {
          "cifras_significativas": 12,
          "decimales_monto": 2,
          "decimales_probabilidad": 8
        },
        "preprocesamiento": {
          "categoricas": [
            {
              "constantes": [
                false,
                false,
                false,
                false
              ],
              "nombre": "producto",
              "vocabulario": [
                "AUTOMOTRIZ",
                "CONSUMO",
                "NOMINA",
                "TARJETA"
              ]
            },
            {
              "constantes": [
                false,
                false,
                false,
                false
              ],
              "nombre": "cve_entidad",
              "vocabulario": [
                "09",
                "15",
                "21",
                "29"
              ]
            },
            {
              "constantes": [
                false,
                false,
                false,
                false
              ],
              "nombre": "tramo_atraso",
              "vocabulario": [
                "0",
                "1-30",
                "31-60",
                "61-90"
              ]
            }
          ],
          "numericas": [
            {
              "constante": false,
              "desviacion": 1.16525514499,
              "indicador_constante": true,
              "indicador_nulo": false,
              "media": 9.86479019196,
              "mediana": 9.80937795175,
              "nombre": "saldo_total",
              "transformacion": "LOG1P"
            },
            {
              "constante": false,
              "desviacion": 1.15513723332,
              "indicador_constante": true,
              "indicador_nulo": true,
              "media": 9.7131841037,
              "mediana": 9.63602958717,
              "nombre": "saldo",
              "transformacion": "LOG1P"
            },
            {
              "constante": false,
              "desviacion": 3.07013889767,
              "indicador_constante": true,
              "indicador_nulo": true,
              "media": 7.04188944924,
              "mediana": 7.62123435851,
              "nombre": "saldo_atrasado",
              "transformacion": "LOG1P"
            },
            {
              "constante": false,
              "desviacion": 3.07013889767,
              "indicador_constante": true,
              "indicador_nulo": true,
              "media": 7.04188944924,
              "mediana": 7.62123435851,
              "nombre": "saldo_requerido",
              "transformacion": "LOG1P"
            }
          ]
        },
        "target": "RECUPERACION_POSITIVA_30D",
        "version_features": "predictive-features/public-v1"
      },
      "baseline_test": {
        "brier": 0.2042496964611716,
        "calibracion_intercepto": -0.18187498728723017,
        "calibracion_pendiente": 0.0,
        "captura_10": 0.11702127659574468,
        "captura_20": 0.18085106382978725,
        "ece": 0.05721871005716059,
        "lift_10": 1.161479834868212,
        "lift_20": 0.9042553191489361,
        "log_loss": 0.5989316602464342,
        "n": 665,
        "pr_auc": 0.28270676691729324,
        "prediccion": 0.3209957418932198,
        "prevalencia": 0.28270676691729324,
        "roc_auc": 0.5
      },
      "bootstrap_test": {
        "cuentas": 665,
        "intervalos": {
          "brier": {
            "estimado": 0.1599662528848734,
            "inferior": 0.1423022891065075,
            "superior": 0.17962372825880715
          },
          "diferencia_brier": {
            "estimado": -0.04428344357629821,
            "inferior": -0.058459582135111404,
            "superior": -0.02901641686780828
          },
          "diferencia_log_loss": {
            "estimado": -0.09652490703897676,
            "inferior": -0.13843392882518035,
            "superior": -0.045214564187081474
          },
          "log_loss": {
            "estimado": 0.5024067532074574,
            "inferior": 0.4514685254973209,
            "superior": 0.5650568037307131
          },
          "pr_auc": {
            "estimado": 0.5779821185128037,
            "inferior": 0.5004527113866114,
            "superior": 0.6481733497358999
          },
          "roc_auc": {
            "estimado": 0.7706298229180606,
            "inferior": 0.7234975660790639,
            "superior": 0.8121340571210732
          }
        },
        "nivel": 0.95,
        "repeticiones": 200,
        "semilla": 2026,
        "unidad": "cuenta"
      },
      "calibracion": "PLATT",
      "compuertas": [
        {
          "compuerta": "LOG_LOSS_NO_PEOR_QUE_BASELINE",
          "ok": true,
          "operador": "<=",
          "umbral": 0.5989316602464342,
          "valor": 0.5024067532074574
        },
        {
          "compuerta": "BRIER_NO_PEOR_QUE_BASELINE",
          "ok": true,
          "operador": "<=",
          "umbral": 0.2042496964611716,
          "valor": 0.1599662528848734
        },
        {
          "compuerta": "ROC_AUC_MAYOR_QUE_0_5",
          "ok": true,
          "operador": ">",
          "umbral": 0.5,
          "valor": 0.7706298229180606
        },
        {
          "compuerta": "PROBABILIDADES_VALIDAS",
          "ok": true,
          "operador": "==",
          "umbral": true,
          "valor": true
        }
      ],
      "estado": "VALIDADO",
      "explicacion_global": [
        {
          "coeficiente": -0.604313876992,
          "columna": "tramo_atraso=91+",
          "direccion": "-",
          "feature": "tramo_atraso",
          "magnitud": 0.604313876992
        },
        {
          "coeficiente": -0.486555566732,
          "columna": "dias_atraso",
          "direccion": "-",
          "feature": "dias_atraso",
          "magnitud": 0.486555566732
        },
        {
          "coeficiente": 0.397321871591,
          "columna": "tramo_atraso=1-30",
          "direccion": "+",
          "feature": "tramo_atraso",
          "magnitud": 0.397321871591
        },
        {
          "coeficiente": -0.338206188625,
          "columna": "tendencia_atraso",
          "direccion": "-",
          "feature": "tendencia_atraso",
          "magnitud": 0.338206188625
        }
      ],
      "hash_artefacto": "7da66ce2e53763de1b704f6aedb48d2b02b476e66d45b35ba66578d66094c3ab",
      "hiperparametros": {
        "c": 0.1,
        "penalizacion": "L2",
        "rebalanceo": null
      },
      "metricas_test": {
        "brier": 0.1599662528848734,
        "calibracion_intercepto": 0.17237974494928962,
        "calibracion_pendiente": 0.7645316181128036,
        "captura_10": 0.24468085106382978,
        "captura_20": 0.4627659574468085,
        "ece": 0.049602812887218045,
        "lift_10": 2.428548745633534,
        "lift_20": 2.3138297872340425,
        "log_loss": 0.5024067532074574,
        "n": 665,
        "pr_auc": 0.5779821185128037,
        "prevalencia": 0.28270676691729324,
        "roc_auc": 0.7706298229180606,
        "sin_calibrar": {
          "brier": 0.16709613411054186,
          "calibracion_intercepto": 0.6609469333634981,
          "calibracion_pendiente": 0.7594449926999821,
          "captura_10": 0.24468085106382978,
          "captura_20": 0.4627659574468085,
          "ece": 0.08513880867669173,
          "lift_10": 2.428548745633534,
          "lift_20": 2.3138297872340425,
          "log_loss": 0.5297867639406745,
          "n": 665,
          "pr_auc": 0.5779821185128037,
          "prevalencia": 0.28270676691729324,
          "roc_auc": 0.7706298229180606
        },
        "tabla_calibracion": [
          {
            "bin": 1,
            "frecuencia_observada": 0.13432835820895522,
            "n": 67,
            "prediccion_maxima": 0.04511006,
            "prediccion_media": 0.03524949805970149,
            "prediccion_minima": 0.00719966
          },
          {
            "bin": 2,
            "frecuencia_observada": 0.13432835820895522,
            "n": 67,
            "prediccion_maxima": 0.05816769,
            "prediccion_media": 0.05112173970149253,
            "prediccion_minima": 0.04528763
          },
          {
            "bin": 3,
            "frecuencia_observada": 0.05970149253731343,
            "n": 67,
            "prediccion_maxima": 0.07359968,
            "prediccion_media": 0.06528744164179105,
            "prediccion_minima": 0.05836726
          },
          {
            "bin": 4,
            "frecuencia_observada": 0.13432835820895522,
            "n": 67,
            "prediccion_maxima": 0.10512158,
            "prediccion_media": 0.0888077276119403,
            "prediccion_minima": 0.07416421
          }
        ]
      },
      "metricas_validation": {
        "calibracion": {
          "brier_validation_platt": 0.15486587135188984,
          "brier_validation_raw": 0.16090100584527295,
          "elegida": "PLATT",
          "mejora_minima": 0.001,
          "platt": {
            "a": 0.479601382056,
            "b": 0.993346745738
          }
        },
        "elegido": {
          "c": 0.1,
          "penalizacion": "L2",
          "rebalanceo": null
        },
        "metricas": {
          "brier": 0.15486587131177806,
          "calibracion_intercepto": -4.402240608370431e-08,
          "calibracion_pendiente": 1.0000000017888855,
          "captura_10": 0.26666666666666666,
          "captura_20": 0.4717948717948718,
          "ece": 0.04986502430091185,
          "lift_10": 2.6585858585858584,
          "lift_20": 2.351825951825952,
          "log_loss": 0.4807586528400036,
          "n": 658,
          "pr_auc": 0.6399598294242204,
          "prevalencia": 0.29635258358662614,
          "roc_auc": 0.7983718225618873
        },
        "rejilla": [
          {
            "brier": 0.16090100584527295,
            "c": 0.1,
            "convergio": true,
            "iteraciones": 5,
            "log_loss": 0.49844090097459826,
            "pr_auc": 0.6399598294242204,
            "roc_auc": 0.7983718225618873
          },
          {
            "brier": 0.16220700882797454,
            "c": 1.0,
            "convergio": true,
            "iteraciones": 5,
            "log_loss": 0.5045505158600792,
            "pr_auc": 0.6348988144457555,
            "roc_auc": 0.7962562994960403
          },
          {
            "brier": 0.1632862884961793,
            "c": 10.0,
            "convergio": true,
            "iteraciones": 5,
            "log_loss": 0.5101284492160779,
            "pr_auc": 0.628904791734188,
            "roc_auc": 0.7946724262059035
          }
        ]
      },
      "target": "RECUPERACION_POSITIVA_30D"
    },
    {
      "algoritmo": "TWEEDIE_LOG",
      "artefacto": {
        "algoritmo": "TWEEDIE_LOG",
        "calibracion": {
          "metodo": "RAW"
        },
        "coeficientes": [
          0.837238223033,
          0.130457690711,
          0.639145002978,
          0.639145002978
        ],
        "columnas": [
          "saldo_total",
          "saldo",
          "saldo_atrasado",
          "saldo_requerido"
        ],
        "formato": "predictive/artifact-v1",
        "hash_features": "786b5ef4b5a21a7583f59082196f77bc4900636ed1089e749fe3a726f9db5e94",
        "hiperparametros": {
          "alpha": 0.01,
          "liga": "log",
          "potencia": 1.5
        },
        "intercepto": 6.13002991695,
        "liga": "log",
        "precision": {
          "cifras_significativas": 12,
          "decimales_monto": 2,
          "decimales_probabilidad": 8
        },
        "preprocesamiento": {
          "categoricas": [
            {
              "constantes": [
                false,
                false,
                false,
                false
              ],
              "nombre": "producto",
              "vocabulario": [
                "AUTOMOTRIZ",
                "CONSUMO",
                "NOMINA",
                "TARJETA"
              ]
            },
            {
              "constantes": [
                false,
                false,
                false,
                false
              ],
              "nombre": "cve_entidad",
              "vocabulario": [
                "09",
                "15",
                "21",
                "29"
              ]
            },
            {
              "constantes": [
                false,
                false,
                false,
                false
              ],
              "nombre": "tramo_atraso",
              "vocabulario": [
                "0",
                "1-30",
                "31-60",
                "61-90"
              ]
            }
          ],
          "numericas": [
            {
              "constante": false,
              "desviacion": 1.16525514499,
              "indicador_constante": true,
              "indicador_nulo": false,
              "media": 9.86479019196,
              "mediana": 9.80937795175,
              "nombre": "saldo_total",
              "transformacion": "LOG1P"
            },
            {
              "constante": false,
              "desviacion": 1.15513723332,
              "indicador_constante": true,
              "indicador_nulo": true,
              "media": 9.7131841037,
              "mediana": 9.63602958717,
              "nombre": "saldo",
              "transformacion": "LOG1P"
            },
            {
              "constante": false,
              "desviacion": 3.07013889767,
              "indicador_constante": true,
              "indicador_nulo": true,
              "media": 7.04188944924,
              "mediana": 7.62123435851,
              "nombre": "saldo_atrasado",
              "transformacion": "LOG1P"
            },
            {
              "constante": false,
              "desviacion": 3.07013889767,
              "indicador_constante": true,
              "indicador_nulo": true,
              "media": 7.04188944924,
              "mediana": 7.62123435851,
              "nombre": "saldo_requerido",
              "transformacion": "LOG1P"
            }
          ]
        },
        "target": "RECUPERACION_POSITIVA_MONTO_30D",
        "version_features": "predictive-features/public-v1"
      },
      "baseline_test": {
        "deciles": [
          {
            "decil": 1,
            "n": 67,
            "predicho_medio": 1258.4408712741567,
            "razon": 1.408597583963065,
            "real_medio": 893.3998507462686,
            "suma_predicha": 84315.5383753685,
            "suma_real": 59857.78999999999
          },
          {
            "decil": 2,
            "n": 67,
            "predicho_medio": 1258.4408712741567,
            "razon": 6.8467113319692645,
            "real_medio": 183.80223880597015,
            "suma_predicha": 84315.5383753685,
            "suma_real": 12314.75
          },
          {
            "decil": 3,
            "n": 67,
            "predicho_medio": 1258.4408712741567,
            "razon": 1.0523013242496215,
            "real_medio": 1195.8940298507462,
            "suma_predicha": 84315.5383753685,
            "suma_real": 80124.9
          },
          {
            "decil": 4,
            "n": 67,
            "predicho_medio": 1258.4408712741567,
            "razon": 0.8400837854090938,
            "real_medio": 1497.99447761194,
            "suma_predicha": 84315.5383753685,
            "suma_real": 100365.62999999999
          }
        ],
        "mae": 2098.0541150501545,
        "n": 665,
        "prediccion": 1258.4408712741567,
        "predicho_medio": 1258.440871274157,
        "razon_sumas": 0.9515590118443146,
        "real_medio": 1322.5042857142857,
        "rmse": 7366.694029615974,
        "suma_predicha": 836863.1793973143,
        "suma_real": 879465.3500000001
      },
      "bootstrap_test": {
        "cuentas": 665,
        "intervalos": {
          "diferencia_mae": {
            "estimado": -379.5667917418841,
            "inferior": -679.7925592328758,
            "superior": 88.1866652740185
          },
          "mae": {
            "estimado": 1718.4873233082703,
            "inferior": 1215.3025808270677,
            "superior": 2539.02082518797
          }
        },
        "nivel": 0.95,
        "repeticiones": 200,
        "semilla": 2026,
        "unidad": "cuenta"
      },
      "calibracion": "RAW",
      "compuertas": [
        {
          "compuerta": "MAE_NO_PEOR_QUE_BASELINE",
          "ok": true,
          "operador": "<=",
          "umbral": 2098.0541150501545,
          "valor": 1718.4873233082703
        },
        {
          "compuerta": "MONTOS_VALIDOS",
          "ok": true,
          "operador": "==",
          "umbral": true,
          "valor": true
        },
        {
          "compuerta": "AJUSTE_CONVERGIO",
          "ok": true,
          "operador": "==",
          "umbral": true,
          "valor": true
        }
      ],
      "estado": "VALIDADO",
      "explicacion_global": [
        {
          "coeficiente": 1.78087737947,
          "columna": "tramo_atraso=0",
          "direccion": "+",
          "feature": "tramo_atraso",
          "magnitud": 1.78087737947
        },
        {
          "coeficiente": -1.22544552464,
          "columna": "tramo_atraso=91+",
          "direccion": "-",
          "feature": "tramo_atraso",
          "magnitud": 1.22544552464
        },
        {
          "coeficiente": 0.837238223033,
          "columna": "saldo_total",
          "direccion": "+",
          "feature": "saldo_total",
          "magnitud": 0.837238223033
        },
        {
          "coeficiente": 0.639145002978,
          "columna": "saldo_atrasado",
          "direccion": "+",
          "feature": "saldo_atrasado",
          "magnitud": 0.639145002978
        }
      ],
      "hash_artefacto": "4ba052ea19b2ee57ae34e3a60790c4e6e7b48d68805f4c0505d5cf00da3cad49",
      "hiperparametros": {
        "alpha": 0.01,
        "liga": "log",
        "potencia": 1.5
      },
      "metricas_test": {
        "deciles": [
          {
            "decil": 1,
            "n": 67,
            "predicho_medio": 18.425671641791045,
            "razon": 0.061216130846182146,
            "real_medio": 300.9937313432836,
            "suma_predicha": 1234.52,
            "suma_real": 20166.58
          },
          {
            "decil": 2,
            "n": 67,
            "predicho_medio": 48.140597014925376,
            "razon": 0.10750511291066604,
            "real_medio": 447.7982089552239,
            "suma_predicha": 3225.42,
            "suma_real": 30002.480000000003
          },
          {
            "decil": 3,
            "n": 67,
            "predicho_medio": 83.75716417910448,
            "razon": 0.8677002671878479,
            "real_medio": 96.52776119402986,
            "suma_predicha": 5611.7300000000005,
            "suma_real": 6467.360000000001
          },
          {
            "decil": 4,
            "n": 67,
            "predicho_medio": 122.1889552238806,
            "razon": 0.32216320701665735,
            "real_medio": 379.27656716417914,
            "suma_predicha": 8186.66,
            "suma_real": 25411.530000000002
          }
        ],
        "mae": 1718.4873233082703,
        "n": 665,
        "predicho_medio": 996.5181052631577,
        "razon_sumas": 0.7535084128101236,
        "real_medio": 1322.5042857142857,
        "rmse": 8637.146010334445,
        "suma_predicha": 662684.5399999999,
        "suma_real": 879465.3500000001
      },
      "metricas_validation": {
        "elegido": {
          "alpha": 0.01,
          "liga": "log",
          "potencia": 1.5
        },
        "metricas": {
          "deciles": [
            {
              "decil": 1,
              "n": 66,
              "predicho_medio": 31.288333333333338,
              "razon": 0.4827295211112203,
              "real_medio": 64.81545454545454,
              "suma_predicha": 2065.03,
              "suma_real": 4277.82
            },
            {
              "decil": 2,
              "n": 66,
              "predicho_medio": 75.40863636363636,
              "razon": 0.5362651926558056,
              "real_medio": 140.6181818181818,
              "suma_predicha": 4976.97,
              "suma_real": 9280.8
            },
            {
              "decil": 3,
              "n": 66,
              "predicho_medio": 125.87242424242424,
              "razon": 0.7628533701127809,
              "real_medio": 165.00212121212124,
              "suma_predicha": 8307.58,
              "suma_real": 10890.140000000001
            },
            {
              "decil": 4,
              "n": 66,
              "predicho_medio": 176.79757575757574,
              "razon": 0.4709449216776149,
              "real_medio": 375.41030303030306,
              "suma_predicha": 11668.64,
              "suma_real": 24777.08
            }
          ],
          "mae": 1716.3451215805471,
          "n": 658,
          "predicho_medio": 1256.8685714285714,
          "razon_sumas": 0.8032587174751393,
          "real_medio": 1564.7120212765958,
          "rmse": 8748.24640328083,
          "suma_predicha": 827019.5199999999,
          "suma_real": 1029580.51
        },
        "rejilla": [
          {
            "alpha": 0.0001,
            "convergio": true,
            "iteraciones": 4,
            "mae": 1723.0076937395493,
            "rmse": 8797.374067287345
          },
          {
            "alpha": 0.001,
            "convergio": true,
            "iteraciones": 4,
            "mae": 1726.9271390105973,
            "rmse": 8823.389578773535
          },
          {
            "alpha": 0.01,
            "convergio": true,
            "iteraciones": 4,
            "mae": 1716.3453740917294,
            "rmse": 8748.246344715513
          }
        ]
      },
      "target": "RECUPERACION_POSITIVA_MONTO_30D"
    }
  ],
  "creada_en": "2026-10-17T13:16:35.949306Z",
  "dataset_hash": "63531824e99322b217a927f3eb21fdca1e4059ad5d756ee441aa7f39eeb5b79e",
  "estado": "PUBLICADA",
  "ficha": {
    "anclas_por_split": {
      "PURGE": 13,
      "TEST": 1,
      "TRAIN": 5,
      "VALIDATION": 1
    },
    "aviso": "predictive/model-v1 estima la probabilidad de resultados observados bajo la operacion historica, con lo que se conocia en cada momento: no mide el efecto de ninguna accion, no recomienda una y no cambia decision/v2, campo/v2 ni ruteo/v2.",
    "cartera": {
      "cartera_id": "PREDICTIVO",
      "despacho_id": "DSP_001"
    },
    "componentes": {
      "CONTACTO_14D": {
        "algoritmo": "LOGISTICA_L2",
        "baseline_test": {
          "brier": 0.23131859354391116,
          "ece": 0.043254385554452796,
          "log_loss": 0.6554139033459765,
          "n": 665,
          "pr_auc": 0.3609022556390977,
          "prevalencia": 0.3609022556390977,
          "roc_auc": 0.5
        },
        "calibracion": "PLATT",
        "estado": "VALIDADO",
        "hiperparametros": {
          "c": 0.1,
          "penalizacion": "L2",
          "rebalanceo": null
        },
        "metricas_test": {
          "brier": 0.21853297510101208,
          "ece": 0.049791309338345864,
          "log_loss": 0.6295826677683475,
          "n": 665,
          "pr_auc": 0.4994678434118984,
          "prevalencia": 0.3609022556390977,
          "roc_auc": 0.637
        }
      },
      "PROMESA_30D": {
        "algoritmo": "LOGISTICA_L2",
        "baseline_test": {
          "brier": 0.14465342018615338,
          "ece": 0.030924839120401733,
          "log_loss": 0.4652853132192698,
          "n": 665,
          "pr_auc": 0.17443609022556392,
          "prevalencia": 0.17443609022556392,
          "roc_auc": 0.5
        },
        "calibracion": "PLATT",
        "estado": "VALIDADO",
        "hiperparametros": {
          "c": 0.1,
          "penalizacion": "L2",
          "rebalanceo": null
        },
        "metricas_test": {
          "brier": 0.14130877843627376,
          "ece": 0.027528624781954888,
          "log_loss": 0.45396531286571723,
          "n": 665,
          "pr_auc": 0.25137795578948186,
          "prevalencia": 0.17443609022556392,
          "roc_auc": 0.6180359273914955
        }
      },
      "RECUPERACION_POSITIVA_30D": {
        "algoritmo": "LOGISTICA_L2",
        "baseline_test": {
          "brier": 0.2042496964611716,
          "ece": 0.05721871005716059,
          "log_loss": 0.5989316602464342,
          "n": 665,
          "pr_auc": 0.28270676691729324,
          "prevalencia": 0.28270676691729324,
          "roc_auc": 0.5
        },
        "calibracion": "PLATT",
        "estado": "VALIDADO",
        "hiperparametros": {
          "c": 0.1,
          "penalizacion": "L2",
          "rebalanceo": null
        },
        "metricas_test": {
          "brier": 0.1599662528848734,
          "ece": 0.049602812887218045,
          "log_loss": 0.5024067532074574,
          "n": 665,
          "pr_auc": 0.5779821185128037,
          "prevalencia": 0.28270676691729324,
          "roc_auc": 0.7706298229180606
        }
      },
      "RECUPERACION_POSITIVA_MONTO_30D": {
        "algoritmo": "TWEEDIE_LOG",
        "baseline_test": {
          "mae": 2098.0541150501545,
          "n": 665,
          "razon_sumas": 0.9515590118443146,
          "rmse": 7366.694029615974,
          "suma_predicha": 836863.1793973143,
          "suma_real": 879465.3500000001
        },
        "calibracion": "RAW",
        "estado": "VALIDADO",
        "hiperparametros": {
          "alpha": 0.01,
          "liga": "log",
          "potencia": 1.5
        },
        "metricas_test": {
          "mae": 1718.4873233082703,
          "n": 665,
          "razon_sumas": 0.7535084128101236,
          "rmse": 8637.146010334445,
          "suma_predicha": 662684.5399999999,
          "suma_real": 879465.3500000001
        }
      }
    },
    "compuertas": {
      "clasificacion": "En TEST, con las probabilidades publicadas: log loss <= la del baseline, Brier <= el del baseline, ROC AUC > 0.5 y toda probabilidad finita en [0, 1]. Si no, el componente queda RECHAZADO.",
      "convergencia": "El ajuste elegido de cada componente convergio.",
      "monto": "En TEST: MAE <= el del baseline, y toda prediccion finita y >= 0."
    },
    "conteos_por_target": {
      "CONTACTO_14D": {
        "PURGE": {
          "censuradas": 1366,
          "maduras": 7236,
          "no_disponibles": 0,
          "prevalencia": 0.35986733
        },
        "TEST": {
          "censuradas": 0,
          "maduras": 665,
          "no_disponibles": 0,
          "prevalencia": 0.36090226
        },
        "TRAIN": {
          "censuradas": 0,
          "maduras": 3053,
          "no_disponibles": 0,
          "prevalencia": 0.33508025
        },
        "VALIDATION": {
          "censuradas": 0,
          "maduras": 658,
          "no_disponibles": 0,
          "prevalencia": 0.33586626
        }
      },
      "PROMESA_30D": {
        "PURGE": {
          "censuradas": 3390,
          "maduras": 5212,
          "no_disponibles": 0,
          "prevalencia": 0.16500384
        },
        "TEST": {
          "censuradas": 0,
          "maduras": 665,
          "no_disponibles": 0,
          "prevalencia": 0.17443609
        },
        "TRAIN": {
          "censuradas": 0,
          "maduras": 3053,
          "no_disponibles": 0,
          "prevalencia": 0.14903374
        },
        "VALIDATION": {
          "censuradas": 0,
          "maduras": 658,
          "no_disponibles": 0,
          "prevalencia": 0.17325228
        }
      },
      "RECUPERACION_POSITIVA_30D": {
        "PURGE": {
          "censuradas": 3390,
          "maduras": 5212,
          "no_disponibles": 0,
          "prevalencia": 0.29662318
        },
        "TEST": {
          "censuradas": 0,
          "maduras": 665,
          "no_disponibles": 0,
          "prevalencia": 0.28270677
        },
        "TRAIN": {
          "censuradas": 0,
          "maduras": 3053,
          "no_disponibles": 0,
          "prevalencia": 0.32099574
        },
        "VALIDATION": {
          "censuradas": 0,
          "maduras": 658,
          "no_disponibles": 0,
          "prevalencia": 0.29635258
        }
      },
      "RECUPERACION_POSITIVA_MONTO_30D": {
        "PURGE": {
          "censuradas": 3390,
          "maduras": 5212,
          "media": 1356.56453185,
          "no_disponibles": 0
        },
        "TEST": {
          "censuradas": 0,
          "maduras": 665,
          "media": 1322.50428571,
          "no_disponibles": 0
        },
        "TRAIN": {
          "censuradas": 0,
          "maduras": 3053,
          "media": 1258.44087127,
          "no_disponibles": 0
        },
        "VALIDATION": {
          "censuradas": 0,
          "maduras": 658,
          "media": 1564.71202128,
          "no_disponibles": 0
        }
      }
    },
    "cuentas_distintas": 855,
    "dataset_hash": "63531824e99322b217a927f3eb21fdca1e4059ad5d756ee441aa7f39eeb5b79e",
    "entorno": {
      "arquitectura": "AMD64",
      "numpy": "2.5.3",
      "pyarrow": "25.0.1",
      "python": "3.14.6",
      "sistema": "Windows"
    },
    "features": {
      "hash": "786b5ef4b5a21a7583f59082196f77bc4900636ed1089e749fe3a726f9db5e94",
      "nombres": [
        "saldo_total",
        "saldo",
        "saldo_atrasado",
        "saldo_requerido"
      ],
      "version": "predictive-features/public-v1"
    },
    "ficha": "predictive/model-v1",
    "filas_por_split": {
      "PURGE": 8602,
      "TEST": 665,
      "TRAIN": 3053,
      "VALIDATION": 658
    },
    "hash_manifiesto": "24da163b996f5562f36fbe93a69b57f56aa08b323d918dc27475574b73ed129a",
    "hash_modelo": "8540230a4df7febc73aab018a11182c56d5fb98166004bacbebebdb334b67b74",
    "hashes_artefactos": {
      "CONTACTO_14D": "83e6390de8d3c078bce0f5bdd4d60760497c7960a22f6e5217f33bee4531f4bf",
      "PROMESA_30D": "1514a69728b3c016cbb020f8458294493915bfc008d21d871ff087e5171cd680",
      "RECUPERACION_POSITIVA_30D": "7da66ce2e53763de1b704f6aedb48d2b02b476e66d45b35ba66578d66094c3ab",
      "RECUPERACION_POSITIVA_MONTO_30D": "4ba052ea19b2ee57ae34e3a60790c4e6e7b48d68805f4c0505d5cf00da3cad49"
    },
    "limitaciones": [
      "Las asociaciones son las de la operacion historica de esta cartera: si la operacion cambia (quien se gestiona, como y cuando), cambian.",
      "Una cuenta sin historia conocida (pocos cortes, ventanas sin cobertura) se predice con imputaciones de TRAIN; su prediccion lo dice en sus motivos.",
      "Las metricas de TEST son de un solo periodo; el walk-forward muestra su variacion.",
      "El generador sintetico fija la relacion entre las features y los resultados: las metricas sobre datos sinteticos no anticipan las de una cartera real."
    ],
    "periodos": {
      "periodo_test_desde": "2026-06-10",
      "periodo_test_hasta": "2026-06-10",
      "periodo_train_desde": "2026-03-04",
      "periodo_train_hasta": "2026-04-01",
      "periodo_validation_desde": "2026-05-06",
      "periodo_validation_hasta": "2026-05-06"
    },
    "privacidad_y_etica": [
      "Ninguna feature es un identificador, un dato personal (nombre, direccion, telefono, correo, CURP, RFC), un atributo sensible (sexo, edad, salud), una coordenada, una geografia mas fina que la entidad ni una salida de decision, campo, ruteo o analytics: predictive-features/public-v1 declara lo que ninguna puede usar.",
      "Las predicciones se publican por cuenta_id, sin CLIENTE_UNICO ni datos personales; el drift y la referencia son agregados, sin una cuenta.",
      "El modelo no decide sobre una persona: corre en sombra y no cambia decision/v2, campo/v2 ni ruteo/v2."
    ],
    "referencia_drift": "Las distribuciones de TRAIN en 10 bins por feature y por salida, y la fraccion de NULL de cada feature, en referencia_drift: un scoring mide su PSI (epsilon 0.0001) contra ellas, con severidad MODERADO desde 0.1 y ALTO desde 0.25. Son heuristicas operacionales: un PSI alto no prueba un deterioro, y el drift no cambia el modelo.",
    "targets": {
      "derivado": "riesgo_sin_recuperacion_30d = 1 - p_recuperacion_positiva_30d, sin modelo propio.",
      "hash": "bc76c4c5b0d60f2f3b628d9f2c8d0b54bdb670c5bc4aace236276ba884cde5b2",
      "targets": {
        "CONTACTO_14D": {
          "horizonte_dias": 14,
          "salida": "p_contacto_14d"
        },
        "PROMESA_30D": {
          "horizonte_dias": 30,
          "salida": "p_promesa_30d"
        },
        "RECUPERACION_POSITIVA_30D": {
          "horizonte_dias": 30,
          "salida": "p_recuperacion_positiva_30d"
        },
        "RECUPERACION_POSITIVA_MONTO_30D": {
          "horizonte_dias": 30,
          "salida": "recuperacion_positiva_esperada_30d"
        }
      },
      "version": "predictive-targets/public-v1"
    },
    "uso_no_previsto": [
      "Estimar el efecto de una accion: una probabilidad alta no dice que gestionar la cuenta cambie su resultado.",
      "Decidir sobre una persona o reemplazar decision/v2, campo/v2 o ruteo/v2.",
      "Usarse en otra cartera, otra zona o un periodo con otra operacion sin medir su drift.",
      "Leer recuperacion_positiva_esperada_30d como la recuperacion neta esperada: los netos negativos se truncaron a cero por definicion."
    ],
    "uso_previsto": "Ordenar las cuentas de la cartera con la que se entreno por la probabilidad de resultados observados en los dias siguientes al corte (recuperacion positiva y contacto, creacion de una promesa) y estimar su recuperacion positiva esperada a 30 dias, como informacion descriptiva en paralelo a decision/v2, en sombra.",
    "zona_horaria": "America/Mexico_City"
  },
  "hash_features": "786b5ef4b5a21a7583f59082196f77bc4900636ed1089e749fe3a726f9db5e94",
  "hash_modelo": "8540230a4df7febc73aab018a11182c56d5fb98166004bacbebebdb334b67b74",
  "hash_politica": "72630f625de5bc53b777da286395474b857d641b810e792c8cb595159973bfe1",
  "hash_targets": "bc76c4c5b0d60f2f3b628d9f2c8d0b54bdb670c5bc4aace236276ba884cde5b2",
  "modelo_version_id": "261a485b-7f86-44f9-808a-190f164fd471",
  "periodo_test_desde": "2026-06-10",
  "periodo_test_hasta": "2026-06-10",
  "periodo_train_desde": "2026-03-04",
  "periodo_train_hasta": "2026-04-01",
  "periodo_validation_desde": "2026-05-06",
  "periodo_validation_hasta": "2026-05-06",
  "publicada_en": "2026-10-17T13:16:35.981951Z",
  "referencia_drift": {
    "bins": 10,
    "epsilon": 0.0001,
    "features": [
      {
        "bordes": [
          8.4582495409,
          8.90237529628,
          9.2148045703,
          9.49299634656
        ],
        "clase": "NUMERICA",
        "faltantes": 0.0,
        "nombre": "saldo_total",
        "proporciones": [
          0.1002292827,
          0.099901736,
          0.099901736,
          0.099901736
        ],
        "transformacion": "LOG1P"
      },
      {
        "bordes": [
          8.32789290991,
          8.78101792734,
          9.09133496453,
          9.35341941538
        ],
        "clase": "NUMERICA",
        "faltantes": 0.0,
        "nombre": "saldo",
        "proporciones": [
          0.1002292827,
          0.1005568293,
          0.0995741893,
          0.1002292827
        ],
        "transformacion": "LOG1P"
      },
      {
        "bordes": [
          0.0,
          5.76676431372,
          6.57238856701,
          7.16028671699
        ],
        "clase": "NUMERICA",
        "faltantes": 0.0,
        "nombre": "saldo_atrasado",
        "proporciones": [
          0.1221749099,
          0.0779561087,
          0.099901736,
          0.099901736
        ],
        "transformacion": "LOG1P"
      },
      {
        "bordes": [
          0.0,
          5.76676431372,
          6.57238856701,
          7.16028671699
        ],
        "clase": "NUMERICA",
        "faltantes": 0.0,
        "nombre": "saldo_requerido",
        "proporciones": [
          0.1221749099,
          0.0779561087,
          0.099901736,
          0.099901736
        ],
        "transformacion": "LOG1P"
      }
    ],
    "filas_train": 3053,
    "salidas": [
      {
        "bordes": [
          0.145389958,
          0.189011948,
          0.258450258,
          0.33857992
        ],
        "clase": "PROBABILIDAD",
        "media": 0.41277688,
        "nombre": "p_recuperacion_positiva_30d",
        "proporciones": [
          0.1002292827,
          0.099901736,
          0.099901736,
          0.099901736
        ]
      },
      {
        "bordes": [
          0.178237256,
          0.206068892,
          0.223805132,
          0.247208934
        ],
        "clase": "PROBABILIDAD",
        "media": 0.2787821,
        "nombre": "p_contacto_14d",
        "proporciones": [
          0.1002292827,
          0.099901736,
          0.099901736,
          0.099901736
        ]
      },
      {
        "bordes": [
          0.172648608,
          0.181023774,
          0.186452098,
          0.191351274
        ],
        "clase": "PROBABILIDAD",
        "media": 0.19788151,
        "nombre": "p_promesa_30d",
        "proporciones": [
          0.1002292827,
          0.099901736,
          0.099901736,
          0.099901736
        ]
      },
      {
        "bordes": [
          84.126,
          145.104,
          215.166,
          307.934
        ],
        "clase": "MONTO",
        "media": 1301.46627252,
        "nombre": "recuperacion_positiva_esperada_30d",
        "proporciones": [
          0.1002292827,
          0.099901736,
          0.099901736,
          0.099901736
        ]
      }
    ]
  },
  "training_run_id": "fed27c20-9a7d-433e-829d-647c29f62d52",
  "version_features": "predictive-features/public-v1",
  "version_modelo": "predictive/model-v1",
  "version_politica": "predictive-training/public-v1",
  "version_targets": "predictive-targets/public-v1"
};

export const drift: Respuesta<"/predictivo/scorings/{scoring_run_id}/drift"> = {
  "aviso": "El drift describe cuanto se parece lo puntuado a TRAIN. No cambia, no recalibra ni reentrena el modelo; sus umbrales son heuristicas operacionales, no leyes estadisticas.",
  "filas": [
    {
      "bins": {
        "bordes": [
          0.0,
          7.0
        ],
        "proporciones_scoring": [
          0.0,
          0.0,
          0.0
        ],
        "proporciones_train": [
          0.1292743953,
          0.8707256047,
          0.0
        ]
      },
      "clase": "NUMERICA",
      "faltantes_scoring": "1.000000",
      "faltantes_train": "0.214543",
      "nombre": "delta_dias_atraso_1_corte",
      "psi": "8.823712",
      "severidad": "ALTO",
      "severidad_faltantes": "ALTO",
      "tipo": "FEATURE"
    },
    {
      "bins": {
        "bordes": [
          -1.457143,
          0.185714,
          1.0
        ],
        "proporciones_scoring": [
          0.0,
          0.0,
          0.0,
          0.0
        ],
        "proporciones_train": [
          0.100500417,
          0.1000834028,
          0.7994161802,
          0.0
        ]
      },
      "clase": "NUMERICA",
      "faltantes_scoring": "1.000000",
      "faltantes_train": "0.214543",
      "nombre": "tendencia_atraso",
      "psi": "8.567815",
      "severidad": "ALTO",
      "severidad_faltantes": "ALTO",
      "tipo": "FEATURE"
    },
    {
      "bins": {
        "bordes": [
          0.69314718056,
          1.09861228867,
          1.38629436112,
          1.79175946923,
          1.94591014906,
          2.30258509299,
          2.48490664979,
          2.7080502011,
          2.94443897917
        ],
        "proporciones_scoring": [
          0.0,
          0.0,
          0.0,
          0.0,
          0.0,
          1.0,
          0.0,
          0.0,
          0.0,
          0.0
        ],
        "proporciones_train": [
          0.1664779162,
          0.0724801812,
          0.066817667,
          0.1415628539,
          0.0566251416,
          0.0906002265,
          0.1313703284,
          0.0724801812,
          0.1087202718,
          0.0928652322
        ]
      },
      "clase": "NUMERICA",
      "faltantes_scoring": "0.998000",
      "faltantes_train": "0.710776",
      "nombre": "dias_desde_ultimo_contacto",
      "psi": "8.525797",
      "severidad": "ALTO",
      "severidad_faltantes": "ALTO",
      "tipo": "FEATURE"
    },
    {
      "bins": {
        "bordes": [
          -34.0,
          -3.0,
          21.0,
          28.0
        ],
        "proporciones_scoring": [
          0.0,
          0.0,
          0.0,
          0.0,
          0.0
        ],
        "proporciones_train": [
          0.1068301226,
          0.0963222417,
          0.1033274956,
          0.6935201401,
          0.0
        ]
      },
      "clase": "NUMERICA",
      "faltantes_scoring": "1.000000",
      "faltantes_train": "0.812971",
      "nombre": "delta_dias_atraso_4_semanas",
      "psi": "8.254702",
      "severidad": "ALTO",
      "severidad_faltantes": "MODERADO",
      "tipo": "FEATURE"
    },
    {
      "bins": {
        "bordes": [
          0.69314718056,
          1.09861228867,
          1.79175946923,
          2.02602898463,
          2.19722457734,
          2.48490664979,
          2.63905732962,
          2.8903717579,
          3.09104245336
        ],
        "proporciones_scoring": [
          0.0,
          0.0,
          0.0,
          0.0,
          1.0,
          0.0,
          0.0,
          0.0,
          0.0,
          0.0
        ],
        "proporciones_train": [
          0.1563636364,
          0.0509090909,
          0.1418181818,
          0.0509090909,
          0.12,
          0.0945454545,
          0.0909090909,
          0.1090909091,
          0.0981818182,
          0.0872727273
        ]
      },
      "clase": "NUMERICA",
      "faltantes_scoring": "0.999000",
      "faltantes_train": "0.909925",
      "nombre": "dias_desde_ultima_promesa",
      "psi": "7.971063",
      "severidad": "ALTO",
      "severidad_faltantes": "MODERADO",
      "tipo": "FEATURE"
    },
    {
      "bins": {
        "bordes": [
          0.0,
          0.69314718056,
          1.09861228867,
          1.38629436112,
          1.60943791243,
          1.94591014906,
          2.07944154168,
          2.3978952728,
          2.63905732962
        ],
        "proporciones_scoring": [
          0.0,
          0.4,
          0.0,
          0.0,
          0.0,
          0.0,
          0.6,
          0.0,
          0.0,
          0.0
        ],
        "proporciones_train": [
          0.1265397536,
          0.101343785,
          0.1041433371,
          0.091825308,
          0.0,
          0.2278835386,
          0.0520716685,
          0.1170212766,
          0.0834266517,
          0.0957446809
        ]
      },
      "clase": "NUMERICA",
      "faltantes_scoring": "0.995000",
      "faltantes_train": "0.415002",
      "nombre": "dias_desde_ultima_gestion",
      "psi": "7.805409",
      "severidad": "ALTO",
      "severidad_faltantes": "ALTO",
      "tipo": "FEATURE"
    },
    {
      "bins": {
        "bordes": [
          1.0,
          2.0,
          3.0,
          4.0,
          5.0
        ],
        "proporciones_scoring": [
          1.0,
          0.0,
          0.0,
          0.0,
          0.0,
          0.0
        ],
        "proporciones_train": [
          0.2145430724,
          0.2060268588,
          0.1994759253,
          0.1929249918,
          0.1870291517,
          0.0
        ]
      },
      "clase": "NUMERICA",
      "faltantes_scoring": "0.000000",
      "faltantes_train": "0.000000",
      "nombre": "numero_cortes_observados",
      "psi": "7.162263",
      "severidad": "ALTO",
      "severidad_faltantes": "BAJO",
      "tipo": "FEATURE"
    },
    {
      "bins": {
        "bordes": [
          -7.93718182042,
          -6.6490881716,
          3.46947888908,
          5.40587047671,
          5.85228868296,
          6.22303251318,
          6.67961174858,
          7.1195061798,
          7.76928143538
        ],
        "proporciones_scoring": [
          0.0,
          0.0,
          0.0,
          0.0,
          0.0,
          0.0,
          0.0,
          0.0,
          0.0,
          0.0
        ],
        "proporciones_train": [
          0.1015761821,
          0.0980735552,
          0.1015761821,
          0.0980735552,
          0.1015761821,
          0.0980735552,
          0.1015761821,
          0.0980735552,
          0.1015761821,
          0.0998248687
        ]
      },
      "clase": "NUMERICA",
      "faltantes_scoring": "1.000000",
      "faltantes_train": "0.812971",
      "nombre": "delta_saldo_4_semanas",
      "psi": "6.900984",
      "severidad": "ALTO",
      "severidad_faltantes": "MODERADO",
      "tipo": "FEATURE"
    },
    {
      "bins": {
        "bordes": [
          -6.07515204209,
          3.6896790669,
          4.23902194026,
          4.5907466645,
          4.88249732742,
          5.23228439594,
          5.5279597546,
          5.92425579741,
          6.62609761468
        ],
        "proporciones_scoring": [
          0.0,
          0.0,
          0.0,
          0.0,
          0.0,
          0.0,
          0.0,
          0.0,
          0.0,
          0.0
        ],
        "proporciones_train": [
          0.1000834028,
          0.1000834028,
          0.100500417,
          0.0992493745,
          0.1000834028,
          0.0992493745,
          0.0996663887,
          0.1000834028,
          0.1009174312,
          0.1000834028
        ]
      },
      "clase": "NUMERICA",
      "faltantes_scoring": "1.000000",
      "faltantes_train": "0.214543",
      "nombre": "delta_saldo_1_corte",
      "psi": "6.900859",
      "severidad": "ALTO",
      "severidad_faltantes": "ALTO",
      "tipo": "FEATURE"
    },
    {
      "bins": {
        "bordes": [
          -4.6173688203,
          -2.76379196953,
          1.98619187878,
          2.44524961849,
          2.76763852332,
          3.13004586143,
          3.4893826638,
          3.90000825653,
          4.59128857065
        ],
        "proporciones_scoring": [
          0.0,
          0.0,
          0.0,
          0.0,
          0.0,
          0.0,
          0.0,
          0.0,
          0.0,
          0.0
        ],
        "proporciones_train": [
          0.1000834028,
          0.1000834028,
          0.1000834028,
          0.0996663887,
          0.1000834028,
          0.1000834028,
          0.1000834028,
          0.1000834028,
          0.0992493745,
          0.100500417
        ]
      },
      "clase": "NUMERICA",
      "faltantes_scoring": "1.000000",
      "faltantes_train": "0.214543",
      "nombre": "tendencia_saldo",
      "psi": "6.900852",
      "severidad": "ALTO",
      "severidad_faltantes": "ALTO",
      "tipo": "FEATURE"
    },
    {
      "bins": {
        "bordes": [
          0.0,
          0.69314718056,
          1.09861228867,
          1.38629436112,
          1.60943791243
        ],
        "proporciones_scoring": [
          0.995,
          0.002,
          0.001,
          0.002,
          0.0,
          0.0
        ],
        "proporciones_train": [
          0.4150016377,
          0.2001310187,
          0.1578774975,
          0.1152964297,
          0.0,
          0.1116934163
        ]
      },
      "clase": "NUMERICA",
      "faltantes_scoring": "0.000000",
      "faltantes_train": "0.000000",
      "nombre": "gestiones_30d",
      "psi": "3.456373",
      "severidad": "ALTO",
      "severidad_faltantes": "BAJO",
      "tipo": "FEATURE"
    },
    {
      "bins": {
        "bordes": [
          0.0,
          0.69314718056,
          1.09861228867,
          1.38629436112,
          1.60943791243
        ],
        "proporciones_scoring": [
          0.995,
          0.002,
          0.001,
          0.002,
          0.0,
          0.0
        ],
        "proporciones_train": [
          0.4150016377,
          0.2001310187,
          0.1578774975,
          0.1152964297,
          0.0,
          0.1116934163
        ]
      },
      "clase": "NUMERICA",
      "faltantes_scoring": "0.000000",
      "faltantes_train": "0.000000",
      "nombre": "gestiones_90d",
      "psi": "3.456373",
      "severidad": "ALTO",
      "severidad_faltantes": "BAJO",
      "tipo": "FEATURE"
    }
  ],
  "modelo_version_id": "261a485b-7f86-44f9-808a-190f164fd471",
  "resumen": {
    "aviso": "El drift describe cuanto se parece lo puntuado a TRAIN. No cambia ni reentrena el modelo; sus umbrales son heuristicas operacionales.",
    "cartera_distinta": true,
    "faltantes": {
      "ALTO": 6,
      "BAJO": 28,
      "MODERADO": 3
    },
    "feature": {
      "ALTO": 16,
      "BAJO": 22,
      "MODERADO": 2
    },
    "mayor_psi": [
      {
        "nombre": "delta_dias_atraso_1_corte",
        "psi": 8.823712,
        "severidad": "ALTO",
        "tipo": "FEATURE"
      },
      {
        "nombre": "tendencia_atraso",
        "psi": 8.567815,
        "severidad": "ALTO",
        "tipo": "FEATURE"
      },
      {
        "nombre": "dias_desde_ultimo_contacto",
        "psi": 8.525797,
        "severidad": "ALTO",
        "tipo": "FEATURE"
      },
      {
        "nombre": "delta_dias_atraso_4_semanas",
        "psi": 8.254702,
        "severidad": "ALTO",
        "tipo": "FEATURE"
      },
      {
        "nombre": "dias_desde_ultima_promesa",
        "psi": 7.971063,
        "severidad": "ALTO",
        "tipo": "FEATURE"
      }
    ],
    "score": {
      "ALTO": 2,
      "BAJO": 1,
      "MODERADO": 1
    }
  },
  "scoring_run_id": "c1397868-406c-431b-ae0a-25a791e574ae"
};

export const predicciones: Respuesta<"/predictivo/scorings/{scoring_run_id}/predicciones"> = {
  "aviso": "predictive/model-v1 estima la probabilidad de resultados observados bajo la operacion historica, con lo que se conocia a known_at. No es un efecto causal, no recomienda una accion y no cambia decision/v2, campo/v2 ni ruteo/v2: corre en paralelo, en sombra.",
  "elementos": [
    {
      "cuenta_id": "261eac0b-af0b-5572-8450-0dfc9d268d07",
      "cve_entidad": "21",
      "estado": "CON_IMPUTACION",
      "feature_hash": "f68567e06f6719eaf387511603ca910a2f3ebdd7c71826f4e9f5a5f350688d87",
      "motivos": [
        {
          "features": [
            "recuperacion_neta_30d_pasados",
            "recuperacion_neta_90d_pasados",
            "movimientos_30d",
            "movimientos_90d",
            "pagos_positivos_30d",
            "pagos_positivos_90d",
            "reversos_90d",
            "dias_desde_ultimo_movimiento",
            "dias_desde_ultima_recuperacion_positiva"
          ],
          "motivo": "SIN_COBERTURA_PAGOS"
        },
        {
          "features": [
            "delta_saldo_1_corte",
            "delta_saldo_4_semanas",
            "delta_dias_atraso_1_corte",
            "delta_dias_atraso_4_semanas",
            "tendencia_saldo",
            "tendencia_atraso"
          ],
          "motivo": "SIN_HISTORIA"
        }
      ],
      "p_contacto_14d": "0.19051500",
      "p_promesa_30d": "0.19775829",
      "p_recuperacion_positiva_30d": "0.47956791",
      "producto": "CONSUMO",
      "recuperacion_positiva_esperada_30d": "2821.95",
      "riesgo_sin_recuperacion_30d": "0.52043209"
    },
    {
      "cuenta_id": "8063b735-17f3-50d5-b006-9a1bba514e97",
      "cve_entidad": "21",
      "estado": "CON_IMPUTACION",
      "feature_hash": "e3131c92f2793efc8a7d7a2533bea7941da78ceba21113147204a3c9f866db4a",
      "motivos": [
        {
          "features": [
            "recuperacion_neta_30d_pasados",
            "recuperacion_neta_90d_pasados",
            "movimientos_30d",
            "movimientos_90d",
            "pagos_positivos_30d",
            "pagos_positivos_90d",
            "reversos_90d",
            "dias_desde_ultimo_movimiento",
            "dias_desde_ultima_recuperacion_positiva"
          ],
          "motivo": "SIN_COBERTURA_PAGOS"
        },
        {
          "features": [
            "delta_saldo_1_corte",
            "delta_saldo_4_semanas",
            "delta_dias_atraso_1_corte",
            "delta_dias_atraso_4_semanas",
            "tendencia_saldo",
            "tendencia_atraso"
          ],
          "motivo": "SIN_HISTORIA"
        }
      ],
      "p_contacto_14d": "0.33644815",
      "p_promesa_30d": "0.20530369",
      "p_recuperacion_positiva_30d": "0.53124705",
      "producto": "TARJETA",
      "recuperacion_positiva_esperada_30d": "1854.45",
      "riesgo_sin_recuperacion_30d": "0.46875295"
    },
    {
      "cuenta_id": "6a566778-9a59-59cd-9d0f-4a89be4c383b",
      "cve_entidad": "30",
      "estado": "CON_IMPUTACION",
      "feature_hash": "b3a2203d4808d2913bc68eba2111bd46639d276655f052adef826d38b59e8750",
      "motivos": [
        {
          "features": [
            "recuperacion_neta_30d_pasados",
            "recuperacion_neta_90d_pasados",
            "movimientos_30d",
            "movimientos_90d",
            "pagos_positivos_30d",
            "pagos_positivos_90d",
            "reversos_90d",
            "dias_desde_ultimo_movimiento",
            "dias_desde_ultima_recuperacion_positiva"
          ],
          "motivo": "SIN_COBERTURA_PAGOS"
        },
        {
          "features": [
            "delta_saldo_1_corte",
            "delta_saldo_4_semanas",
            "delta_dias_atraso_1_corte",
            "delta_dias_atraso_4_semanas",
            "tendencia_saldo",
            "tendencia_atraso"
          ],
          "motivo": "SIN_HISTORIA"
        }
      ],
      "p_contacto_14d": "0.23333342",
      "p_promesa_30d": "0.20762187",
      "p_recuperacion_positiva_30d": "0.53421614",
      "producto": "CONSUMO",
      "recuperacion_positiva_esperada_30d": "845.20",
      "riesgo_sin_recuperacion_30d": "0.46578386"
    },
    {
      "cuenta_id": "740638fb-356f-5b51-a342-7e6adc0227da",
      "cve_entidad": "30",
      "estado": "CON_IMPUTACION",
      "feature_hash": "063df6dbdc1f45d6a11028eec65ef0caad1311b20e23a8154c16b8c1169d15e6",
      "motivos": [
        {
          "features": [
            "recuperacion_neta_30d_pasados",
            "recuperacion_neta_90d_pasados",
            "movimientos_30d",
            "movimientos_90d",
            "pagos_positivos_30d",
            "pagos_positivos_90d",
            "reversos_90d",
            "dias_desde_ultimo_movimiento",
            "dias_desde_ultima_recuperacion_positiva"
          ],
          "motivo": "SIN_COBERTURA_PAGOS"
        },
        {
          "features": [
            "delta_saldo_1_corte",
            "delta_saldo_4_semanas",
            "delta_dias_atraso_1_corte",
            "delta_dias_atraso_4_semanas",
            "tendencia_saldo",
            "tendencia_atraso"
          ],
          "motivo": "SIN_HISTORIA"
        }
      ],
      "p_contacto_14d": "0.33369710",
      "p_promesa_30d": "0.22777314",
      "p_recuperacion_positiva_30d": "0.25226050",
      "producto": "TARJETA",
      "recuperacion_positiva_esperada_30d": "280.30",
      "riesgo_sin_recuperacion_30d": "0.74773950"
    },
    {
      "cuenta_id": "02c6dfad-c9cb-51cb-bf56-d32f0fa7a69b",
      "cve_entidad": "21",
      "estado": "CON_IMPUTACION",
      "feature_hash": "a9d539e9b661451ec0550f0d066f096d648ecc0a10842c547162bc746c7ef69c",
      "motivos": [
        {
          "features": [
            "recuperacion_neta_30d_pasados",
            "recuperacion_neta_90d_pasados",
            "movimientos_30d",
            "movimientos_90d",
            "pagos_positivos_30d",
            "pagos_positivos_90d",
            "reversos_90d",
            "dias_desde_ultimo_movimiento",
            "dias_desde_ultima_recuperacion_positiva"
          ],
          "motivo": "SIN_COBERTURA_PAGOS"
        },
        {
          "features": [
            "delta_saldo_1_corte",
            "delta_saldo_4_semanas",
            "delta_dias_atraso_1_corte",
            "delta_dias_atraso_4_semanas",
            "tendencia_saldo",
            "tendencia_atraso"
          ],
          "motivo": "SIN_HISTORIA"
        }
      ],
      "p_contacto_14d": "0.11690921",
      "p_promesa_30d": "0.18907727",
      "p_recuperacion_positiva_30d": "0.62723841",
      "producto": "CONSUMO",
      "recuperacion_positiva_esperada_30d": "1033.04",
      "riesgo_sin_recuperacion_30d": "0.37276159"
    }
  ],
  "pagina": 1,
  "por_pagina": 5,
  "scoring_run_id": "c1397868-406c-431b-ae0a-25a791e574ae",
  "total": 1000
};

export const explicacionPredictiva: Respuesta<"/predictivo/scorings/{scoring_run_id}/cuentas/{cuenta_id}/explicacion"> = {
  "as_of": "2026-09-30",
  "aviso": "Una contribucion es una asociacion del modelo con su target, en la escala de su predictor lineal: no dice que cambiar esa feature cambie el resultado, ni recomienda una accion.",
  "componentes": [
    {
      "calibracion": {
        "a": 0.479601382056,
        "b": 0.993346745738,
        "metodo": "PLATT"
      },
      "escala": "log-odds de la probabilidad cruda",
      "hacia_abajo": [
        {
          "contribucion": -0.604313876992,
          "feature": "tramo_atraso"
        },
        {
          "contribucion": -0.2509945994207024,
          "feature": "dias_desde_ultima_gestion"
        },
        {
          "contribucion": -0.111811762911,
          "feature": "producto"
        },
        {
          "contribucion": -0.07262081515643487,
          "feature": "tendencia_atraso"
        },
        {
          "contribucion": -0.0549949016380349,
          "feature": "tendencia_saldo"
        }
      ],
      "hacia_arriba": [
        {
          "contribucion": 0.191852261172,
          "feature": "cve_entidad"
        },
        {
          "contribucion": 0.1656302909224565,
          "feature": "numero_cortes_observados"
        },
        {
          "contribucion": 0.07295306031944111,
          "feature": "gestiones_30d"
        },
        {
          "contribucion": 0.07295306031944111,
          "feature": "gestiones_90d"
        },
        {
          "contribucion": 0.056365451249068825,
          "feature": "atraso_maximo"
        }
      ],
      "intercepto": -0.879408930251,
      "prediccion_publicada": "0.26167243",
      "prediccion_reproducida": "0.26167243",
      "predictor_lineal": -1.5270553779468452,
      "salida": "p_recuperacion_positiva_30d",
      "suma_de_contribuciones": -0.6476464476958449,
      "target": "RECUPERACION_POSITIVA_30D",
      "verificacion": {
        "reproduce_lo_publicado": true,
        "suma_igual_al_predictor": true
      }
    },
    {
      "calibracion": {
        "a": -0.358522856918,
        "b": 0.873227640584,
        "metodo": "PLATT"
      },
      "escala": "log-odds de la probabilidad cruda",
      "hacia_abajo": [
        {
          "contribucion": -0.117592165238,
          "feature": "cve_entidad"
        },
        {
          "contribucion": -0.097593420811504,
          "feature": "dias_desde_ultima_gestion"
        },
        {
          "contribucion": -0.09746419990450933,
          "feature": "delta_saldo_4_semanas"
        },
        {
          "contribucion": -0.07332059479616404,
          "feature": "dias_desde_ultimo_contacto"
        },
        {
          "contribucion": -0.0695889560381278,
          "feature": "contactos_90d"
        }
      ],
      "hacia_arriba": [
        {
          "contribucion": 0.15114919058011783,
          "feature": "gestiones_sin_contacto_30d"
        },
        {
          "contribucion": 0.14359505531601732,
          "feature": "dias_desde_ultima_promesa"
        },
        {
          "contribucion": 0.08483329335998711,
          "feature": "saldo_atrasado"
        },
        {
          "contribucion": 0.08483329335998711,
          "feature": "saldo_requerido"
        },
        {
          "contribucion": 0.05361360780286328,
          "feature": "gestiones_30d"
        }
      ],
      "intercepto": -0.604062104982,
      "prediccion_publicada": "0.29328641",
      "prediccion_reproducida": "0.29328641",
      "predictor_lineal": -0.5965832605526895,
      "salida": "p_contacto_14d",
      "suma_de_contribuciones": 0.007478844429310065,
      "target": "CONTACTO_14D",
      "verificacion": {
        "reproduce_lo_publicado": true,
        "suma_igual_al_predictor": true
      }
    },
    {
      "calibracion": {
        "a": -0.892213181544,
        "b": 0.281123294643,
        "metodo": "PLATT"
      },
      "escala": "log-odds de la probabilidad cruda",
      "hacia_abajo": [
        {
          "contribucion": -0.09319316478288205,
          "feature": "contactos_90d"
        },
        {
          "contribucion": -0.09319316478288205,
          "feature": "contactos_30d"
        },
        {
          "contribucion": -0.08153083591719165,
          "feature": "atraso_maximo"
        },
        {
          "contribucion": -0.0444908804785338,
          "feature": "dias_desde_ultima_gestion"
        },
        {
          "contribucion": -0.03407064081920515,
          "feature": "tendencia_saldo"
        }
      ],
      "hacia_arriba": [
        {
          "contribucion": 0.394619653045,
          "feature": "tramo_atraso"
        },
        {
          "contribucion": 0.2716593979901302,
          "feature": "numero_cortes_observados"
        },
        {
          "contribucion": 0.21137281365851268,
          "feature": "dias_desde_ultimo_contacto"
        },
        {
          "contribucion": 0.192716098575,
          "feature": "producto"
        },
        {
          "contribucion": 0.09497527586150087,
          "feature": "moratorios"
        }
      ],
      "intercepto": -2.10252115009,
      "prediccion_publicada": "0.23634787",
      "prediccion_reproducida": "0.23634787",
      "predictor_lineal": -0.9981187697751196,
      "salida": "p_promesa_30d",
      "suma_de_contribuciones": 1.1044023803148797,
      "target": "PROMESA_30D",
      "verificacion": {
        "reproduce_lo_publicado": true,
        "suma_igual_al_predictor": true
      }
    },
    {
      "calibracion": {
        "metodo": "RAW"
      },
      "escala": "log de la recuperacion positiva esperada",
      "hacia_abajo": [
        {
          "contribucion": -1.22544552464,
          "feature": "tramo_atraso"
        },
        {
          "contribucion": -0.27423545152393763,
          "feature": "moratorios"
        },
        {
          "contribucion": -0.22371822529900576,
          "feature": "delta_saldo_4_semanas"
        },
        {
          "contribucion": -0.10407338175812987,
          "feature": "gestiones_90d"
        },
        {
          "contribucion": -0.10407338175812987,
          "feature": "gestiones_30d"
        }
      ],
      "hacia_arriba": [
        {
          "contribucion": 0.3453436091890972,
          "feature": "saldo_atrasado"
        },
        {
          "contribucion": 0.3453436091890972,
          "feature": "saldo_requerido"
        },
        {
          "contribucion": 0.277689823018,
          "feature": "cve_entidad"
        },
        {
          "contribucion": 0.26950939051659695,
          "feature": "dias_desde_ultimo_contacto"
        },
        {
          "contribucion": 0.15602393487627075,
          "feature": "numero_cortes_observados"
        }
      ],
      "intercepto": 6.13002991695,
      "prediccion_publicada": "241.62",
      "prediccion_reproducida": "241.62",
      "predictor_lineal": 5.487354333695775,
      "salida": "recuperacion_positiva_esperada_30d",
      "suma_de_contribuciones": -0.6426755832542244,
      "target": "RECUPERACION_POSITIVA_MONTO_30D",
      "verificacion": {
        "reproduce_lo_publicado": true,
        "suma_igual_al_predictor": true
      }
    }
  ],
  "cuenta_id": "41095c46-babd-53a1-a59b-231da3e9d99a",
  "estado": "CON_IMPUTACION",
  "feature_hash": "aad459d9bac5fdd12c59cd51702b7ebbeae38ef5c35f4a702e6b2693d0ffd7f3",
  "features": {
    "atraso_maximo": "195",
    "contactos_30d": "0",
    "contactos_90d": "0",
    "cve_entidad": "21",
    "delta_dias_atraso_1_corte": null,
    "delta_dias_atraso_4_semanas": null,
    "delta_saldo_1_corte": null,
    "delta_saldo_4_semanas": null,
    "dias_atraso": "141",
    "dias_desde_ultima_gestion": null,
    "dias_desde_ultima_promesa": null,
    "dias_desde_ultima_recuperacion_positiva": null,
    "dias_desde_ultimo_contacto": null,
    "dias_desde_ultimo_movimiento": null,
    "gestiones_30d": "0",
    "gestiones_90d": "0",
    "gestiones_sin_contacto_30d": "0",
    "moratorios": "3145.66",
    "movimientos_30d": null,
    "movimientos_90d": null,
    "numero_cortes_observados": "1",
    "pago_normal": "286.02",
    "pagos_positivos_30d": null,
    "pagos_positivos_90d": null,
    "producto": "CONSUMO",
    "promesas_creadas_90d": "0",
    "promesas_cumplidas_historicas": "0",
    "promesas_incumplidas_historicas": "0",
    "promesas_parciales_historicas": "0",
    "promesas_pendientes_actuales": "0",
    "recuperacion_neta_30d_pasados": null,
    "recuperacion_neta_90d_pasados": null,
    "reversos_90d": null,
    "saldo": "14873.12",
    "saldo_atrasado": "6006.42",
    "saldo_requerido": "6006.42",
    "saldo_total": "18018.78",
    "tendencia_atraso": null,
    "tendencia_saldo": null,
    "tramo_atraso": "91+"
  },
  "known_at": "2026-10-17T13:14:29.444442Z",
  "modelo_version_id": "261a485b-7f86-44f9-808a-190f164fd471",
  "motivos": [
    {
      "features": [
        "recuperacion_neta_30d_pasados",
        "recuperacion_neta_90d_pasados",
        "movimientos_30d",
        "movimientos_90d",
        "pagos_positivos_30d",
        "pagos_positivos_90d",
        "reversos_90d",
        "dias_desde_ultimo_movimiento",
        "dias_desde_ultima_recuperacion_positiva"
      ],
      "motivo": "SIN_COBERTURA_PAGOS"
    },
    {
      "features": [
        "delta_saldo_1_corte",
        "delta_saldo_4_semanas",
        "delta_dias_atraso_1_corte",
        "delta_dias_atraso_4_semanas",
        "tendencia_saldo",
        "tendencia_atraso"
      ],
      "motivo": "SIN_HISTORIA"
    }
  ],
  "riesgo_sin_recuperacion_30d": "0.73832757",
  "scoring_run_id": "c1397868-406c-431b-ae0a-25a791e574ae"
};

export const optimizacion: Respuesta<"/optimizacion/ejecuciones/{optimizacion_run_id}"> = {
  "aviso": "Este es el optimo del modelo de asignacion declarado: asigna capacidad escasa a oportunidades predichas bajo la politica historica, con los supuestos de su configuracion. No demuestra que el canal asignado cause el resultado predicho, no estima un efecto incremental y no ejecuta ninguna accion: es un plan de soporte a decision, en sombra. Un optimo matematico de supuestos incorrectos sigue siendo una mala decision de negocio.",
  "best_bound": "30197437.0000",
  "campo_run_id": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
  "certificado": {
    "estado": "OPTIMAL",
    "modelo": {
      "filas": {
        "CAPACIDAD_POOL": 5,
        "UNA_ASIGNACION": 9
      },
      "matriz": "dispersa (scipy.sparse CSR)",
      "no_nulos": 66,
      "restricciones": 14,
      "variables": 48
    },
    "niveles": [
      {
        "codigo_highs": 7,
        "estado": "OPTIMAL",
        "fijadas_por_dualidad": 47,
        "gap_relativo": 0.0,
        "invocado": true,
        "iteraciones_relajacion": 8,
        "mejor_cota": 30197437.0,
        "mensaje": "Optimization terminated successfully. (HiGHS Status 7: Optimal)",
        "metodo": "RELAJACION_LP_ENTERA",
        "nivel": 1,
        "no_nulos": 66,
        "nodos": 0,
        "objetivo": 30197437,
        "restricciones": 14,
        "segundos": 0.002753,
        "sentido": "MAXIMIZAR",
        "variables": 48
      },
      {
        "codigo_highs": 7,
        "estado": "OPTIMAL",
        "fijadas_por_dualidad": 0,
        "gap_relativo": 0.0,
        "invocado": true,
        "iteraciones_relajacion": 5,
        "mejor_cota": 1132709418.0,
        "mensaje": "Optimization terminated successfully. (HiGHS Status 7: Optimal)",
        "metodo": "RELAJACION_LP_ENTERA",
        "nivel": 2,
        "no_nulos": 2,
        "nodos": 0,
        "objetivo": 1132709418,
        "restricciones": 2,
        "segundos": 0.002562,
        "sentido": "MAXIMIZAR",
        "variables": 1
      },
      {
        "codigo_highs": 7,
        "estado": "OPTIMAL",
        "fijadas_por_dualidad": 0,
        "gap_relativo": 0.0,
        "invocado": true,
        "iteraciones_relajacion": 3,
        "mejor_cota": 240.0,
        "mensaje": "Optimization terminated successfully. (HiGHS Status 7: Optimal)",
        "metodo": "RELAJACION_LP_ENTERA",
        "nivel": 3,
        "no_nulos": 3,
        "nodos": 0,
        "objetivo": 240,
        "restricciones": 3,
        "segundos": 0.002639,
        "sentido": "MINIMIZAR",
        "variables": 1
      },
      {
        "codigo_highs": 7,
        "estado": "OPTIMAL",
        "fijadas_por_dualidad": 0,
        "gap_relativo": 0.0,
        "invocado": true,
        "iteraciones_relajacion": 3,
        "mejor_cota": 168666.0,
        "mensaje": "Optimization terminated successfully. (HiGHS Status 7: Optimal)",
        "metodo": "RELAJACION_LP_ENTERA",
        "nivel": 4,
        "no_nulos": 4,
        "nodos": 0,
        "objetivo": 168666,
        "restricciones": 4,
        "segundos": 0.00271,
        "sentido": "MINIMIZAR",
        "variables": 1
      }
    ],
    "reduccion": {
      "aplicada": true,
      "clases": 5,
      "clases_por_cota": {
        "CAPACIDAD_POOLS": 2,
        "TAMANO": 3
      },
      "elegibles": 1000,
      "eliminadas": 961,
      "porcentaje_eliminado": 96.1,
      "retenidas": 39
    },
    "resultado": "PLAN_OPTIMO"
  },
  "clases": 5,
  "configuracion_id": "ec6c6274-a2a0-4999-b2f9-5721c78c5bda",
  "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
  "cuentas_asignadas": 39,
  "cuentas_con_imputacion": 1000,
  "cuentas_elegibles": 1000,
  "cuentas_no_elegibles": 0,
  "cuentas_publicadas": 1000,
  "cuentas_retenidas": 39,
  "cuentas_universo": 1000,
  "decision_run_id": "13c166a9-218f-435d-a36c-5aea9eaee03e",
  "desempate_canonico": 168666,
  "detalle": "Plan optimo de 39 cuentas de 1,000 del corte del 2026-09-30, para el 2026-10-01: valor de oportunidad predicho de 301,974.37 y 240 unidades de recurso. HiGHS demostro el optimo de los cuatro niveles (OPTIMAL, cota = objetivo). Este es el optimo del modelo de asignacion declarado: asigna capacidad escasa a oportunidades predichas bajo la politica historica, con los supuestos de su configuracion. No demuestra que el canal asignado cause el resultado predicho, no estima un efecto incremental y no ejecuta ninguna accion: es un plan de soporte a decision, en sombra. Un optimo matematico de supuestos incorrectos sigue siendo una mala decision de negocio.",
  "duracion_segundos": 0.637683,
  "elementos_no_nulos": 66,
  "estado": "EXITOSA",
  "fecha_corte": "2026-09-30",
  "fecha_operacion": "2026-10-01",
  "firma_inputs": "1526f329dfdd26da67b3ec4c59e3857985e2a1574774f1d03d2a2ec0f676fd77",
  "hash_configuracion": "2bf5ffa23f64bbf9daac085582645ea197fc93ea1ebad73ff72e8dccaa232e47",
  "hash_plan": "5a8eddbe67ed16056a25931fbd1bdc1998a07c9c61b86f548d6ca1e66c6a7e01",
  "hash_politica": "540bc21fadd8297c2a8d427e8af540610642016e7dea5e9be87f997c53d23fc5",
  "huella_solver": {
    "algoritmo": "SCIPY_HIGHS_MILP/v1",
    "arquitectura": "AMD64",
    "highs": "1.12.0",
    "numpy": "2.5.3",
    "parametros": {
      "metodo": "relajacion lineal (highs-ipm con crossover); si su vertice no es entero, branch-and-bound (milp)",
      "mip_rel_gap": 0.0,
      "presolve": false,
      "time_limit_por_nivel": 1800.0,
      "tolerancia_entera_lp": 1e-09
    },
    "python": "3.14.6",
    "scipy": "1.18.1",
    "sistema": "Windows"
  },
  "iniciada_en": "2026-10-17T13:16:44.547155Z",
  "limitaciones": [
    "Es el optimo del modelo de asignacion declarado, no una estrategia causalmente optima.",
    "El valor es la recuperacion positiva esperada bajo la politica historica, no el beneficio incremental de contactar.",
    "Un canal asignado es una asignacion de capacidad compatible con las restricciones, no la evidencia de que ese canal cause mayor recuperacion.",
    "Las capacidades de un benchmark son sinteticas, y las unidades de recurso no son costos reales.",
    "Un optimo matematico de supuestos incorrectos sigue siendo una mala decision de negocio.",
    "Es un plan de soporte a decision, en sombra: no registra gestiones, no envia mensajes, no llama, no crea visitas ni cambia decision/v2, campo/v2 o ruteo/v2."
  ],
  "mip_gap": "0.000000000000",
  "modelo_version_id": "261a485b-7f86-44f9-808a-190f164fd471",
  "objetivo_primario_centavos": 30197437,
  "objetivo_secundario": 1132709418,
  "optimizacion_run_id": "bb5fda47-20a1-4bf9-a466-69102e76b965",
  "restricciones_solver": 14,
  "resultado": "PLAN_PUBLICADO",
  "resumen": {
    "con_imputacion": {
      "admitidas": true,
      "asignadas": 39,
      "elegibles": 1000,
      "universo": 1000
    },
    "drift_scoring": {
      "nota": "El drift del scoring declarado se reporta y no cambia el objetivo: un PSI alto no prueba un mal desempeno.",
      "resumen": {
        "aviso": "El drift describe cuanto se parece lo puntuado a TRAIN. No cambia ni reentrena el modelo; sus umbrales son heuristicas operacionales.",
        "cartera_distinta": true,
        "faltantes": {
          "ALTO": 6,
          "BAJO": 28,
          "MODERADO": 3
        },
        "feature": {
          "ALTO": 16,
          "BAJO": 22,
          "MODERADO": 2
        },
        "mayor_psi": [
          {
            "nombre": "delta_dias_atraso_1_corte",
            "psi": 8.823712,
            "severidad": "ALTO",
            "tipo": "FEATURE"
          },
          {
            "nombre": "tendencia_atraso",
            "psi": 8.567815,
            "severidad": "ALTO",
            "tipo": "FEATURE"
          },
          {
            "nombre": "dias_desde_ultimo_contacto",
            "psi": 8.525797,
            "severidad": "ALTO",
            "tipo": "FEATURE"
          },
          {
            "nombre": "delta_dias_atraso_4_semanas",
            "psi": 8.254702,
            "severidad": "ALTO",
            "tipo": "FEATURE"
          },
          {
            "nombre": "dias_desde_ultima_promesa",
            "psi": 7.971063,
            "severidad": "ALTO",
            "tipo": "FEATURE"
          }
        ],
        "score": {
          "ALTO": 2,
          "BAJO": 1,
          "MODERADO": 1
        }
      }
    },
    "estados": {
      "ASIGNADA": 39,
      "NO_ELEGIBLE": 0,
      "NO_SELECCIONADA": 961
    },
    "motivos": {
      "EN_SOLUCION_OPTIMA": 39,
      "FUERA_DE_SOLUCION_OPTIMA": 961
    },
    "por_canal": {
      "CAMPO": {
        "capacidad": 9,
        "pools": 3,
        "unidades": 180,
        "utilizacion": 1.0,
        "utilizada": 9,
        "valor_centavos": 286879
      },
      "DIGITAL": {
        "capacidad": 20,
        "pools": 1,
        "unidades": 20,
        "utilizacion": 1.0,
        "utilizada": 20,
        "valor_centavos": 20302238
      },
      "TELEFONICA": {
        "capacidad": 10,
        "pools": 1,
        "unidades": 40,
        "utilizacion": 1.0,
        "utilizada": 10,
        "valor_centavos": 9608320
      }
    },
    "reduccion": {
      "aplicada": true,
      "clases": 5,
      "clases_por_cota": {
        "CAPACIDAD_POOLS": 2,
        "TAMANO": 3
      },
      "elegibles": 1000,
      "eliminadas": 961,
      "porcentaje_eliminado": 96.1,
      "retenidas": 39
    },
    "restricciones": {
      "binding": 5,
      "binding_declaradas": [],
      "holgura_minima": 0,
      "no_binding": 0,
      "total": 5
    },
    "valor_oportunidad_centavos": {
      "elegibles": 111894724,
      "plan": 30197437,
      "universo": 111894724
    }
  },
  "ruteo_run_id": "4730febb-aa0c-4372-8918-04387d31fe71",
  "scoring_run_id": "c1397868-406c-431b-ae0a-25a791e574ae",
  "solver_fingerprint": "f6707745bf17d65321020c56e65191c12324015c6cf4ad6c4a145b908ff2568f",
  "solver_seconds": "0.011",
  "solver_status": "OPTIMAL",
  "terminada_en": "2026-10-17T13:16:45.184838Z",
  "tiempos": {
    "fases_segundos": {
      "plan": 0.005,
      "publicacion": 0.036,
      "solver": 0.016,
      "staging": 0.004,
      "universo": 0.063
    },
    "memoria_pico_mb": 275.9,
    "solver_segundos": {
      "modelo": 0.0,
      "nivel_1": 0.003,
      "nivel_2": 0.003,
      "nivel_3": 0.003,
      "nivel_4": 0.003,
      "reduccion": 0.001
    },
    "total_segundos": 0.128
  },
  "trabajo_id": "54c31c4a-5b30-4c36-9250-262a2cabe51c",
  "unidades_recurso": 240,
  "variables": 48,
  "version_motor": "optimization/v1",
  "version_politica": "optimization-public-v1"
};

export const capacidad: Respuesta<"/optimizacion/ejecuciones/{optimizacion_run_id}/capacidad"> = {
  "elementos": [
    {
      "canal": "CAMPO",
      "capacidad": 3,
      "capacidad_declarada": null,
      "elegibles": 3,
      "holgura": 0,
      "pool": "CAMPO_GESTOR_RUTA_001",
      "referencia_alcance": "GESTOR_RUTA_001",
      "retenidas": 3,
      "tipo_alcance": "GESTOR_CAMPO",
      "unidades_consumidas": 60,
      "unidades_por_asignacion": 20,
      "utilizacion": "1.000000",
      "utilizada": 3
    },
    {
      "canal": "CAMPO",
      "capacidad": 4,
      "capacidad_declarada": null,
      "elegibles": 4,
      "holgura": 0,
      "pool": "CAMPO_GESTOR_RUTA_002",
      "referencia_alcance": "GESTOR_RUTA_002",
      "retenidas": 4,
      "tipo_alcance": "GESTOR_CAMPO",
      "unidades_consumidas": 80,
      "unidades_por_asignacion": 20,
      "utilizacion": "1.000000",
      "utilizada": 4
    },
    {
      "canal": "CAMPO",
      "capacidad": 2,
      "capacidad_declarada": null,
      "elegibles": 2,
      "holgura": 0,
      "pool": "CAMPO_GESTOR_RUTA_003",
      "referencia_alcance": "GESTOR_RUTA_003",
      "retenidas": 2,
      "tipo_alcance": "GESTOR_CAMPO",
      "unidades_consumidas": 40,
      "unidades_por_asignacion": 20,
      "utilizacion": "1.000000",
      "utilizada": 2
    },
    {
      "canal": "DIGITAL",
      "capacidad": 20,
      "capacidad_declarada": 20,
      "elegibles": 546,
      "holgura": 0,
      "pool": "DIGITAL_GLOBAL",
      "referencia_alcance": null,
      "retenidas": 20,
      "tipo_alcance": "GLOBAL",
      "unidades_consumidas": 20,
      "unidades_por_asignacion": 1,
      "utilizacion": "1.000000",
      "utilizada": 20
    },
    {
      "canal": "TELEFONICA",
      "capacidad": 10,
      "capacidad_declarada": 10,
      "elegibles": 454,
      "holgura": 0,
      "pool": "TELEFONICA_GLOBAL",
      "referencia_alcance": null,
      "retenidas": 19,
      "tipo_alcance": "GLOBAL",
      "unidades_consumidas": 40,
      "unidades_por_asignacion": 4,
      "utilizacion": "1.000000",
      "utilizada": 10
    }
  ],
  "optimizacion_run_id": "bb5fda47-20a1-4bf9-a466-69102e76b965",
  "pagina": 1,
  "por_canal": {
    "CAMPO": {
      "capacidad": 9,
      "pools": 3,
      "unidades": 180,
      "utilizacion": 1.0,
      "utilizada": 9,
      "valor_centavos": 286879
    },
    "DIGITAL": {
      "capacidad": 20,
      "pools": 1,
      "unidades": 20,
      "utilizacion": 1.0,
      "utilizada": 20,
      "valor_centavos": 20302238
    },
    "TELEFONICA": {
      "capacidad": 10,
      "pools": 1,
      "unidades": 40,
      "utilizacion": 1.0,
      "utilizada": 10,
      "valor_centavos": 9608320
    }
  },
  "por_pagina": 50,
  "total": 5
};

export const restricciones: Respuesta<"/optimizacion/ejecuciones/{optimizacion_run_id}/restricciones"> = {
  "elementos": [
    {
      "binding": true,
      "codigo": "POOL:CAMPO_GESTOR_RUTA_001",
      "cuentas_grupo": null,
      "dimension": null,
      "holgura": 0,
      "lhs": 3,
      "limite_resuelto": null,
      "operador": "<=",
      "rhs": 3,
      "tipo": "CAPACIDAD_POOL",
      "total": null,
      "valor_dimension": null
    },
    {
      "binding": true,
      "codigo": "POOL:CAMPO_GESTOR_RUTA_002",
      "cuentas_grupo": null,
      "dimension": null,
      "holgura": 0,
      "lhs": 4,
      "limite_resuelto": null,
      "operador": "<=",
      "rhs": 4,
      "tipo": "CAPACIDAD_POOL",
      "total": null,
      "valor_dimension": null
    },
    {
      "binding": true,
      "codigo": "POOL:CAMPO_GESTOR_RUTA_003",
      "cuentas_grupo": null,
      "dimension": null,
      "holgura": 0,
      "lhs": 2,
      "limite_resuelto": null,
      "operador": "<=",
      "rhs": 2,
      "tipo": "CAPACIDAD_POOL",
      "total": null,
      "valor_dimension": null
    },
    {
      "binding": true,
      "codigo": "POOL:DIGITAL_GLOBAL",
      "cuentas_grupo": null,
      "dimension": null,
      "holgura": 0,
      "lhs": 20,
      "limite_resuelto": null,
      "operador": "<=",
      "rhs": 20,
      "tipo": "CAPACIDAD_POOL",
      "total": null,
      "valor_dimension": null
    },
    {
      "binding": true,
      "codigo": "POOL:TELEFONICA_GLOBAL",
      "cuentas_grupo": null,
      "dimension": null,
      "holgura": 0,
      "lhs": 10,
      "limite_resuelto": null,
      "operador": "<=",
      "rhs": 10,
      "tipo": "CAPACIDAD_POOL",
      "total": null,
      "valor_dimension": null
    }
  ],
  "optimizacion_run_id": "bb5fda47-20a1-4bf9-a466-69102e76b965",
  "pagina": 1,
  "por_pagina": 50,
  "total": 5
};

export const resultados: Respuesta<"/optimizacion/ejecuciones/{optimizacion_run_id}/resultados"> = {
  "aviso": "Este es el optimo del modelo de asignacion declarado: asigna capacidad escasa a oportunidades predichas bajo la politica historica, con los supuestos de su configuracion. No demuestra que el canal asignado cause el resultado predicho, no estima un efecto incremental y no ejecuta ninguna accion: es un plan de soporte a decision, en sombra. Un optimo matematico de supuestos incorrectos sigue siendo una mala decision de negocio.",
  "elementos": [
    {
      "atraso_bucket": "31-60",
      "base": "BASE_RUTEO",
      "canal_asignado": null,
      "canal_decision": "CAMPO",
      "cuenta_id": "261eac0b-af0b-5572-8450-0dfc9d268d07",
      "estado": "NO_SELECCIONADA",
      "gestor": null,
      "motivo": "FUERA_DE_SOLUCION_OPTIMA",
      "orden_ruteo_original": null,
      "p_contacto_14d": "0.19051500",
      "pool": null,
      "prioridad": "MUY_ALTA",
      "producto": "CONSUMO",
      "retenida": false,
      "unidades_recurso": null,
      "valor_oportunidad_centavos": 282195
    },
    {
      "atraso_bucket": "31-60",
      "base": "BASE_RUTEO",
      "canal_asignado": null,
      "canal_decision": "TELEFONICA",
      "cuenta_id": "8063b735-17f3-50d5-b006-9a1bba514e97",
      "estado": "NO_SELECCIONADA",
      "gestor": null,
      "motivo": "FUERA_DE_SOLUCION_OPTIMA",
      "orden_ruteo_original": null,
      "p_contacto_14d": "0.33644815",
      "pool": null,
      "prioridad": "ALTA",
      "producto": "TARJETA",
      "retenida": false,
      "unidades_recurso": null,
      "valor_oportunidad_centavos": 185445
    },
    {
      "atraso_bucket": "1-30",
      "base": null,
      "canal_asignado": null,
      "canal_decision": "DIGITAL",
      "cuenta_id": "6a566778-9a59-59cd-9d0f-4a89be4c383b",
      "estado": "NO_SELECCIONADA",
      "gestor": null,
      "motivo": "FUERA_DE_SOLUCION_OPTIMA",
      "orden_ruteo_original": null,
      "p_contacto_14d": "0.23333342",
      "pool": null,
      "prioridad": "MEDIA",
      "producto": "CONSUMO",
      "retenida": false,
      "unidades_recurso": null,
      "valor_oportunidad_centavos": 84520
    },
    {
      "atraso_bucket": "91+",
      "base": null,
      "canal_asignado": null,
      "canal_decision": "CAMPO",
      "cuenta_id": "740638fb-356f-5b51-a342-7e6adc0227da",
      "estado": "NO_SELECCIONADA",
      "gestor": null,
      "motivo": "FUERA_DE_SOLUCION_OPTIMA",
      "orden_ruteo_original": null,
      "p_contacto_14d": "0.33369710",
      "pool": null,
      "prioridad": "MUY_ALTA",
      "producto": "TARJETA",
      "retenida": false,
      "unidades_recurso": null,
      "valor_oportunidad_centavos": 28030
    },
    {
      "atraso_bucket": "0",
      "base": null,
      "canal_asignado": null,
      "canal_decision": "DIGITAL",
      "cuenta_id": "02c6dfad-c9cb-51cb-bf56-d32f0fa7a69b",
      "estado": "NO_SELECCIONADA",
      "gestor": null,
      "motivo": "FUERA_DE_SOLUCION_OPTIMA",
      "orden_ruteo_original": null,
      "p_contacto_14d": "0.11690921",
      "pool": null,
      "prioridad": "BAJA",
      "producto": "CONSUMO",
      "retenida": false,
      "unidades_recurso": null,
      "valor_oportunidad_centavos": 103304
    }
  ],
  "optimizacion_run_id": "bb5fda47-20a1-4bf9-a466-69102e76b965",
  "pagina": 1,
  "por_pagina": 5,
  "total": 1000
};

export const diagnostico: Respuesta<"/optimizacion/ejecuciones/{optimizacion_run_id}/diagnostico"> = {
  "certificado": {
    "estado": "OPTIMAL",
    "modelo": {
      "filas": {
        "CAPACIDAD_POOL": 5,
        "UNA_ASIGNACION": 9
      },
      "matriz": "dispersa (scipy.sparse CSR)",
      "no_nulos": 66,
      "restricciones": 14,
      "variables": 48
    },
    "niveles": [
      {
        "codigo_highs": 7,
        "estado": "OPTIMAL",
        "fijadas_por_dualidad": 47,
        "gap_relativo": 0.0,
        "invocado": true,
        "iteraciones_relajacion": 8,
        "mejor_cota": 30197437.0,
        "mensaje": "Optimization terminated successfully. (HiGHS Status 7: Optimal)",
        "metodo": "RELAJACION_LP_ENTERA",
        "nivel": 1,
        "no_nulos": 66,
        "nodos": 0,
        "objetivo": 30197437,
        "restricciones": 14,
        "segundos": 0.002753,
        "sentido": "MAXIMIZAR",
        "variables": 48
      },
      {
        "codigo_highs": 7,
        "estado": "OPTIMAL",
        "fijadas_por_dualidad": 0,
        "gap_relativo": 0.0,
        "invocado": true,
        "iteraciones_relajacion": 5,
        "mejor_cota": 1132709418.0,
        "mensaje": "Optimization terminated successfully. (HiGHS Status 7: Optimal)",
        "metodo": "RELAJACION_LP_ENTERA",
        "nivel": 2,
        "no_nulos": 2,
        "nodos": 0,
        "objetivo": 1132709418,
        "restricciones": 2,
        "segundos": 0.002562,
        "sentido": "MAXIMIZAR",
        "variables": 1
      },
      {
        "codigo_highs": 7,
        "estado": "OPTIMAL",
        "fijadas_por_dualidad": 0,
        "gap_relativo": 0.0,
        "invocado": true,
        "iteraciones_relajacion": 3,
        "mejor_cota": 240.0,
        "mensaje": "Optimization terminated successfully. (HiGHS Status 7: Optimal)",
        "metodo": "RELAJACION_LP_ENTERA",
        "nivel": 3,
        "no_nulos": 3,
        "nodos": 0,
        "objetivo": 240,
        "restricciones": 3,
        "segundos": 0.002639,
        "sentido": "MINIMIZAR",
        "variables": 1
      },
      {
        "codigo_highs": 7,
        "estado": "OPTIMAL",
        "fijadas_por_dualidad": 0,
        "gap_relativo": 0.0,
        "invocado": true,
        "iteraciones_relajacion": 3,
        "mejor_cota": 168666.0,
        "mensaje": "Optimization terminated successfully. (HiGHS Status 7: Optimal)",
        "metodo": "RELAJACION_LP_ENTERA",
        "nivel": 4,
        "no_nulos": 4,
        "nodos": 0,
        "objetivo": 168666,
        "restricciones": 4,
        "segundos": 0.00271,
        "sentido": "MINIMIZAR",
        "variables": 1
      }
    ],
    "reduccion": {
      "aplicada": true,
      "clases": 5,
      "clases_por_cota": {
        "CAPACIDAD_POOLS": 2,
        "TAMANO": 3
      },
      "elegibles": 1000,
      "eliminadas": 961,
      "porcentaje_eliminado": 96.1,
      "retenidas": 39
    },
    "resultado": "PLAN_OPTIMO"
  },
  "detalle": "Plan optimo de 39 cuentas de 1,000 del corte del 2026-09-30, para el 2026-10-01: valor de oportunidad predicho de 301,974.37 y 240 unidades de recurso. HiGHS demostro el optimo de los cuatro niveles (OPTIMAL, cota = objetivo). Este es el optimo del modelo de asignacion declarado: asigna capacidad escasa a oportunidades predichas bajo la politica historica, con los supuestos de su configuracion. No demuestra que el canal asignado cause el resultado predicho, no estima un efecto incremental y no ejecuta ninguna accion: es un plan de soporte a decision, en sombra. Un optimo matematico de supuestos incorrectos sigue siendo una mala decision de negocio.",
  "diagnostico": {
    "hallazgos": []
  },
  "estado": "EXITOSA",
  "optimizacion_run_id": "bb5fda47-20a1-4bf9-a466-69102e76b965",
  "plan_publicado": true,
  "resultado": "PLAN_PUBLICADO",
  "solver_status": "OPTIMAL"
};

export const explicacionOptimizacion: Respuesta<"/optimizacion/ejecuciones/{optimizacion_run_id}/cuentas/{cuenta_id}"> = {
  "aviso": "Este es el optimo del modelo de asignacion declarado: asigna capacidad escasa a oportunidades predichas bajo la politica historica, con los supuestos de su configuracion. No demuestra que el canal asignado cause el resultado predicho, no estima un efecto incremental y no ejecuta ninguna accion: es un plan de soporte a decision, en sombra. Un optimo matematico de supuestos incorrectos sigue siendo una mala decision de negocio.",
  "canales_admisibles": [
    "CAMPO",
    "TELEFONICA"
  ],
  "cuenta_id": "41095c46-babd-53a1-a59b-231da3e9d99a",
  "desempate": "p_contacto_14d solo desempata entre planes del mismo valor de oportunidad: no es la probabilidad de exito del canal asignado.",
  "explicacion": "El plan optimo publicado la asigna al pool CAMPO_GESTOR_RUTA_002 (CAMPO). Es una asignacion de capacidad compatible con las restricciones, no la evidencia de que ese canal cause la recuperacion predicha.",
  "grupos": [],
  "hechos": {
    "base_territorial": "BASE_RUTEO",
    "canal_recomendado_decision_v2": "CAMPO",
    "configuracion_admite_imputacion": true,
    "parada_ruteo_v2": {
      "estado": "PROGRAMADA",
      "gestor": "GESTOR_RUTA_002",
      "motivo": null
    },
    "prediccion": "CON_IMPUTACION",
    "prioridad_decision_v2": "MUY_ALTA"
  },
  "optimizacion_run_id": "bb5fda47-20a1-4bf9-a466-69102e76b965",
  "pools_elegibles": [
    "CAMPO_GESTOR_RUTA_002",
    "TELEFONICA_GLOBAL"
  ],
  "resultado": {
    "atraso_bucket": "91+",
    "base": "BASE_RUTEO",
    "canal_asignado": "CAMPO",
    "canal_decision": "CAMPO",
    "cuenta_id": "41095c46-babd-53a1-a59b-231da3e9d99a",
    "estado": "ASIGNADA",
    "gestor": "GESTOR_RUTA_002",
    "motivo": "EN_SOLUCION_OPTIMA",
    "orden_ruteo_original": 1,
    "p_contacto_14d": "0.29328641",
    "pool": "CAMPO_GESTOR_RUTA_002",
    "prioridad": "MUY_ALTA",
    "producto": "CONSUMO",
    "retenida": true,
    "unidades_recurso": 20,
    "valor_oportunidad_centavos": 24162
  }
};

export const planCampo: Respuesta<"/campo-v2/planes/{campo_run_id}"> = {
  "aviso": "campo/v2 dice que candidata de campo de una decision/v2 quedo con que gestor de su base territorial en una fecha, bajo la capacidad declarada (una cuenta por unidad), y en que particion operativa. Es capacidad declarada, no factibilidad vial: no usa distancias, tiempos de traslado ni rutas (v0.13), y no mueve cuentas entre bases. Sin coordenadas.",
  "bases_version_id": "bc965622-644b-4608-b584-5ea59e8f0ab9",
  "campo_run_id": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
  "capacidad": {
    "capacidad_total": 49,
    "capacidad_utilizada": 24,
    "utilizacion": "0.4898"
  },
  "configuracion": {
    "configuracion_campo_version_id": "4cece266-3c7a-4ff1-bc62-d2a64989e9ac",
    "hash_configuracion": "900d22efd85fffd64c6454d570625938d5033f4cf107551a2e1ba1e60d73354a",
    "nombre": "Campo de ruteo de la prueba de humo",
    "zona_horaria": "America/Mexico_City"
  },
  "conteos": {
    "asignadas": 24,
    "candidatas_campo": 153,
    "cuentas_evaluadas_decision": 1000,
    "rebalanceadas": 11,
    "revision_territorial": 129,
    "sin_capacidad": 0,
    "sin_gestor": 0
  },
  "corte": {
    "corte_id": "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b",
    "fecha_corte": "2026-09-30"
  },
  "decision_run_id": "13c166a9-218f-435d-a36c-5aea9eaee03e",
  "detalle": "Se planearon las 153 candidatas de campo de 1,000 cuentas decididas, para el 2026-10-01, con campo-public-v2: 24 asignadas a un gestor de su base (11 por spillover), 129 en revision territorial, 0 sin gestor disponible y 0 sin capacidad; 24 de 49 de capacidad declarada. Capacidad declarada, no factibilidad vial. campo/v2.",
  "distribucion": {
    "balance": {
      "coeficiente_variacion_cuentas": "0.6374",
      "cuentas_por_gestor": {
        "maximo": "15.0000",
        "mediana": "6.0000",
        "minimo": "3.0000",
        "p95": "15.0000"
      },
      "microtramos_por_gestor": {
        "maximo": "11.0000",
        "mediana": "3.0000",
        "minimo": "1.0000",
        "p95": "11.0000"
      },
      "rebalanceadas_por_gestor": {
        "maximo": "9.0000",
        "mediana": "2.0000",
        "minimo": "0.0000",
        "p95": "9.0000"
      },
      "utilizacion": {
        "maximo": "1.0000",
        "mediana": "1.0000",
        "minimo": "0.3750",
        "p95": "1.0000"
      }
    },
    "estado": {
      "ASIGNADA": 24,
      "REVISION_TERRITORIAL": 129,
      "SIN_CAPACIDAD": 0,
      "SIN_GESTOR_DISPONIBLE": 0
    },
    "gestores": 3,
    "gestores_disponibles": 3,
    "jornada": {
      "JORNADA_SEMANAL": 3
    },
    "motivos": {
      "PROPIETARIO_PREFERIDO": 13,
      "SPILLOVER_POR_CAPACIDAD": 11,
      "TERRITORIAL_NO_ASIGNADA": 129
    },
    "por_base": {
      "BASE_RUTEO": {
        "asignadas": 24,
        "candidatas": 24,
        "capacidad": 49,
        "gestores": 3,
        "gestores_disponibles": 3,
        "rebalanceadas": 11,
        "sin_capacidad": 0,
        "sin_gestor": 0,
        "utilizacion": "0.4898"
      }
    },
    "unidades": {
      "MICROTRAMO": 15,
      "SUBZONA": 1,
      "TRAMO": 2,
      "ZONA": 1
    }
  },
  "duracion_segundos": 0.532,
  "estado": "EXITOSA",
  "fecha_operacion": "2026-10-01",
  "firma_inputs": "52fdbb54ae8f48a9733e33f1b83813495a8b91b45be19ed89301422da1bc81b7",
  "hash_politica": "b8d51c1441cc93a7b0d22416ac258bb134c70608bfef5ab1e37a6d7c59ecd854",
  "iniciada_en": "2026-10-17T13:14:39.341688Z",
  "particion": {
    "microtramo_m": 1000,
    "redondeo_m": "0.001",
    "srid": 6372,
    "tramo_m": 5000
  },
  "resultado": "PLAN_PUBLICADO",
  "terminada_en": "2026-10-17T13:14:39.873764Z",
  "territorial_run_id": "d89b3565-7416-47a0-b4e7-e5bf22bc3e0b",
  "trabajo_id": "4b56c4b1-b429-4e13-ada7-b74ca7e9b7de",
  "version_motor": "campo/v2",
  "version_politica": "campo-public-v2"
};

export const cargas: Respuesta<"/campo-v2/planes/{campo_run_id}/cargas"> = {
  "campo_run_id": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
  "elementos": [
    {
      "asignadas": 3,
      "base": {
        "base_id": "faa1f6d4-ad78-45c9-b56a-028054f2c675",
        "codigo": "BASE_RUTEO"
      },
      "capacidad": 3,
      "estado_jornada": "JORNADA_SEMANAL",
      "gestor": {
        "codigo": "GESTOR_RUTA_001",
        "gestor_id": "867d4427-3042-4936-8416-f39b2148ef8e"
      },
      "microtramos": 1,
      "rebalanceadas": 0,
      "tramos": 1,
      "utilizacion": "1.0000",
      "ventanas": [
        {
          "fin": "19:00",
          "fin_en": "2026-10-02T01:00:00Z",
          "inicio": "09:00",
          "inicio_en": "2026-10-01T15:00:00Z"
        }
      ]
    },
    {
      "asignadas": 6,
      "base": {
        "base_id": "faa1f6d4-ad78-45c9-b56a-028054f2c675",
        "codigo": "BASE_RUTEO"
      },
      "capacidad": 6,
      "estado_jornada": "JORNADA_SEMANAL",
      "gestor": {
        "codigo": "GESTOR_RUTA_002",
        "gestor_id": "2fb917e5-02ff-4be4-8b91-8b9e0a1596e0"
      },
      "microtramos": 3,
      "rebalanceadas": 2,
      "tramos": 1,
      "utilizacion": "1.0000",
      "ventanas": [
        {
          "fin": "09:45",
          "fin_en": "2026-10-01T15:45:00Z",
          "inicio": "09:00",
          "inicio_en": "2026-10-01T15:00:00Z"
        },
        {
          "fin": "14:00",
          "fin_en": "2026-10-01T20:00:00Z",
          "inicio": "11:00",
          "inicio_en": "2026-10-01T17:00:00Z"
        }
      ]
    },
    {
      "asignadas": 15,
      "base": {
        "base_id": "faa1f6d4-ad78-45c9-b56a-028054f2c675",
        "codigo": "BASE_RUTEO"
      },
      "capacidad": 40,
      "estado_jornada": "JORNADA_SEMANAL",
      "gestor": {
        "codigo": "GESTOR_RUTA_003",
        "gestor_id": "5d915053-bd8e-474e-9fc8-d46c727759f4"
      },
      "microtramos": 11,
      "rebalanceadas": 9,
      "tramos": 2,
      "utilizacion": "0.3750",
      "ventanas": [
        {
          "fin": "09:40",
          "fin_en": "2026-10-01T15:40:00Z",
          "inicio": "09:00",
          "inicio_en": "2026-10-01T15:00:00Z"
        }
      ]
    }
  ],
  "pagina": 1,
  "por_pagina": 50,
  "total": 3
};

export const ruteo: Respuesta<"/ruteo-v2/ejecuciones/{ruteo_run_id}"> = {
  "aviso": "ruteo/v2 dice en que orden puede recorrer cada gestor, por la red vial, las cuentas que campo/v2 le asigno, con ETA planificadas sobre tiempos estaticos de la red (sin trafico) y un servicio fijo de 900 s por visita, que es una hipotesis publica. Es una heuristica determinista, no una ruta optima. No reasigna cuentas entre gestores ni entre bases. Sin coordenadas ni geometrias.",
  "campo_run_id": "581de30e-9e76-4b3a-a722-bf0289e1b25b",
  "conteos": {
    "bases_no_enrutables": 0,
    "gestores_objetivo": 3,
    "no_enrutables": 3,
    "no_enrutables_base": 0,
    "paradas_objetivo": 24,
    "programadas": 9,
    "rutas_completas": 1,
    "rutas_parciales": 2,
    "rutas_sin_jornada": 0,
    "rutas_sin_paradas": 0,
    "sin_tiempo": 12
  },
  "detalle": "Se rutearon 24 cuentas ASIGNADA de 3 gestores para el 2026-10-01, con ruteo-public-v2: 9 programadas, 12 sin tiempo, 3 no enrutables y 0 sin base enrutable; 1 rutas completas y 2 parciales; 10,598 m por calle y 1,544 s de traslado. Una heuristica determinista, no una ruta optima, sin trafico. ruteo/v2.",
  "distribucion": {
    "2opt": {
      "mejoras": 1,
      "rutas_mejoradas": 1
    },
    "matrices": {
      "bloques": 3,
      "celdas": 182,
      "matrices": 3,
      "puntos": {
        "maximo": 13,
        "media": 6.0,
        "mediana": 3,
        "minimo": 2,
        "p95": 13
      }
    },
    "motivos": {
      "JORNADA_AGOTADA": 12,
      "RED_DESCONECTADA": 1,
      "SNAP_EXCESIVO": 2
    },
    "paradas": {
      "NO_ENRUTABLE": 3,
      "NO_ENRUTABLE_BASE": 0,
      "PROGRAMADA": 9,
      "SIN_TIEMPO": 12
    },
    "por_base": {
      "BASE_RUTEO": {
        "completas": 1,
        "distancia_m": 10598,
        "no_enrutables": 3,
        "paradas": 24,
        "parciales": 2,
        "programadas": 9,
        "rutas": 3,
        "sin_tiempo": 12,
        "traslado_s": 1544
      }
    },
    "por_prioridad": {
      "MUY_ALTA": {
        "paradas": 24,
        "programadas": 9,
        "sin_tiempo": 12
      }
    },
    "rutas": {
      "BASE_NO_ENRUTABLE": 0,
      "COMPLETA": 1,
      "PARCIAL": 2,
      "SIN_JORNADA": 0,
      "SIN_PARADAS_ENRUTABLES": 0
    },
    "snap": {
      "excesivos": 1,
      "puntos_consultados": 17,
      "sin_snap": 0
    }
  },
  "duracion_segundos": 0.54,
  "estado": "EXITOSA",
  "fecha_operacion": "2026-10-01",
  "firma_inputs": "7362dd512045817fd08491b126e8a94a2193bb08b229f531f30e71219dcfdb09",
  "hash_politica": "6afe7b063d0151adfbac89efe6c49979a9507dc7257175e3fa514c3ebf31b097",
  "iniciada_en": "2026-10-17T13:14:40.497369Z",
  "politica": {
    "algoritmo": "PRIORITY_NEAREST_NEIGHBOR_2OPT/v1",
    "bloque_matriz": 100,
    "duracion_servicio_s": 900,
    "inicio_fin_base": true,
    "max_mejoras_2opt": 100,
    "max_snap_distancia_m": 500,
    "perfil": "car"
  },
  "red": {
    "huella": "1211063510392e330808ab038debded8817f2498d87057b13861bb056c5f29d8",
    "motor_ruteo": "OSRM",
    "red_vial_version_id": "84d173b2-0b93-4838-97ac-2af88b18e5fc",
    "region": "Red sintetica de la prueba de humo (municipio sintetico 21114)",
    "version_motor_ruteo": "v26.9.0"
  },
  "resultado": "RUTAS_PUBLICADAS",
  "ruteo_run_id": "4730febb-aa0c-4372-8918-04387d31fe71",
  "terminada_en": "2026-10-17T13:14:41.037405Z",
  "totales": {
    "distancia_total_m": 10598,
    "duracion_espera_total_s": 4774,
    "duracion_servicio_total_s": 8100,
    "duracion_vial_total_s": 1544
  },
  "trabajo_id": "cb8ee89e-9bd9-4db4-a555-45ba2b9f014a",
  "version_motor": "ruteo/v2",
  "version_politica": "ruteo-public-v2",
  "zona_horaria": "America/Mexico_City"
};

export const rutas: Respuesta<"/ruteo-v2/ejecuciones/{ruteo_run_id}/rutas"> = {
  "elementos": [
    {
      "base": {
        "base_id": "faa1f6d4-ad78-45c9-b56a-028054f2c675",
        "codigo": "BASE_RUTEO"
      },
      "base_snap_distancia_m": "0.00",
      "conteos": {
        "no_enrutables": 0,
        "no_enrutables_base": 0,
        "paradas_campo": 3,
        "programadas": 3,
        "sin_tiempo": 0
      },
      "distancia_total_m": 2990,
      "duracion_espera_s": 0,
      "duracion_servicio_s": 2700,
      "duracion_total_s": 3138,
      "duracion_traslado_s": 438,
      "estado_ruta": "COMPLETA",
      "fecha_operacion": "2026-10-01",
      "gestor": {
        "codigo": "GESTOR_RUTA_001",
        "gestor_id": "867d4427-3042-4936-8416-f39b2148ef8e"
      },
      "hash_matriz": "84c2a0a0a94d2c95d0518295ff43e234821e31b0e900c104674d976a276e7799",
      "hash_secuencia": "77f5d7a0bf6087e6f8dd56f82a2971f708673ae77c192c7216444d2040f755c8",
      "jornada_fin": "2026-10-01T19:00:00-06:00",
      "jornada_inicio": "2026-10-01T09:00:00-06:00",
      "mejoras_2opt": 0,
      "motivo_base": null,
      "regreso": {
        "distancia_m": 1495,
        "duracion_s": 221,
        "espera_s": 0,
        "llegada": "2026-10-01T09:52:18-06:00",
        "salida": "2026-10-01T09:48:37-06:00"
      },
      "ruta_id": "14fba0a4-9e1a-5004-80ac-7db545d4a2ee",
      "ruteo_run_id": "4730febb-aa0c-4372-8918-04387d31fe71",
      "salida_base": "2026-10-01T09:00:00-06:00",
      "zona_horaria": "America/Mexico_City"
    },
    {
      "base": {
        "base_id": "faa1f6d4-ad78-45c9-b56a-028054f2c675",
        "codigo": "BASE_RUTEO"
      },
      "base_snap_distancia_m": "0.00",
      "conteos": {
        "no_enrutables": 2,
        "no_enrutables_base": 0,
        "paradas_campo": 6,
        "programadas": 4,
        "sin_tiempo": 0
      },
      "distancia_total_m": 5798,
      "duracion_espera_s": 4774,
      "duracion_servicio_s": 3600,
      "duracion_total_s": 9216,
      "duracion_traslado_s": 842,
      "estado_ruta": "PARCIAL",
      "fecha_operacion": "2026-10-01",
      "gestor": {
        "codigo": "GESTOR_RUTA_002",
        "gestor_id": "2fb917e5-02ff-4be4-8b91-8b9e0a1596e0"
      },
      "hash_matriz": "024f2db3054beca619e6d97159cd0f6d254d473d9ef10cf3920b65ec84a3299f",
      "hash_secuencia": "49ef83362a29ce15d24b41b383dcefae25ec89f200c71c63d3c89c3ec4a35362",
      "jornada_fin": "2026-10-01T14:00:00-06:00",
      "jornada_inicio": "2026-10-01T09:00:00-06:00",
      "mejoras_2opt": 0,
      "motivo_base": null,
      "regreso": {
        "distancia_m": 1501,
        "duracion_s": 216,
        "espera_s": 0,
        "llegada": "2026-10-01T11:33:36-06:00",
        "salida": "2026-10-01T11:30:00-06:00"
      },
      "ruta_id": "8399224e-90bc-5ee6-a282-8b58a249cecd",
      "ruteo_run_id": "4730febb-aa0c-4372-8918-04387d31fe71",
      "salida_base": "2026-10-01T09:00:00-06:00",
      "zona_horaria": "America/Mexico_City"
    },
    {
      "base": {
        "base_id": "faa1f6d4-ad78-45c9-b56a-028054f2c675",
        "codigo": "BASE_RUTEO"
      },
      "base_snap_distancia_m": "0.00",
      "conteos": {
        "no_enrutables": 1,
        "no_enrutables_base": 0,
        "paradas_campo": 15,
        "programadas": 2,
        "sin_tiempo": 12
      },
      "distancia_total_m": 1810,
      "duracion_espera_s": 0,
      "duracion_servicio_s": 1800,
      "duracion_total_s": 2064,
      "duracion_traslado_s": 264,
      "estado_ruta": "PARCIAL",
      "fecha_operacion": "2026-10-01",
      "gestor": {
        "codigo": "GESTOR_RUTA_003",
        "gestor_id": "5d915053-bd8e-474e-9fc8-d46c727759f4"
      },
      "hash_matriz": "69b3b84e59137071add8275936d88545931f1bcadb8635dbef39a51c5630163e",
      "hash_secuencia": "78e6fb7d9d7cda8390c2bb745a278031d1066aa4a322220300b33f2ddcb5e8b7",
      "jornada_fin": "2026-10-01T09:40:00-06:00",
      "jornada_inicio": "2026-10-01T09:00:00-06:00",
      "mejoras_2opt": 1,
      "motivo_base": null,
      "regreso": {
        "distancia_m": 60,
        "duracion_s": 9,
        "espera_s": 0,
        "llegada": "2026-10-01T09:34:24-06:00",
        "salida": "2026-10-01T09:34:15-06:00"
      },
      "ruta_id": "9b4a1d9f-30bc-51ab-8c29-62c6291b2868",
      "ruteo_run_id": "4730febb-aa0c-4372-8918-04387d31fe71",
      "salida_base": "2026-10-01T09:00:00-06:00",
      "zona_horaria": "America/Mexico_City"
    }
  ],
  "pagina": 1,
  "por_pagina": 50,
  "ruteo_run_id": "4730febb-aa0c-4372-8918-04387d31fe71",
  "total": 3
};

export const ruta: Respuesta<"/ruteo-v2/rutas/{ruta_id}"> = {
  "base": {
    "base_id": "faa1f6d4-ad78-45c9-b56a-028054f2c675",
    "codigo": "BASE_RUTEO"
  },
  "base_snap_distancia_m": "0.00",
  "conteos": {
    "no_enrutables": 2,
    "no_enrutables_base": 0,
    "paradas_campo": 6,
    "programadas": 4,
    "sin_tiempo": 0
  },
  "distancia_total_m": 5798,
  "duracion_espera_s": 4774,
  "duracion_servicio_s": 3600,
  "duracion_total_s": 9216,
  "duracion_traslado_s": 842,
  "estado_ruta": "PARCIAL",
  "fecha_operacion": "2026-10-01",
  "gestor": {
    "codigo": "GESTOR_RUTA_002",
    "gestor_id": "2fb917e5-02ff-4be4-8b91-8b9e0a1596e0"
  },
  "hash_matriz": "024f2db3054beca619e6d97159cd0f6d254d473d9ef10cf3920b65ec84a3299f",
  "hash_secuencia": "49ef83362a29ce15d24b41b383dcefae25ec89f200c71c63d3c89c3ec4a35362",
  "jornada_fin": "2026-10-01T14:00:00-06:00",
  "jornada_inicio": "2026-10-01T09:00:00-06:00",
  "mejoras_2opt": 0,
  "motivo_base": null,
  "regreso": {
    "distancia_m": 1501,
    "duracion_s": 216,
    "espera_s": 0,
    "llegada": "2026-10-01T11:33:36-06:00",
    "salida": "2026-10-01T11:30:00-06:00"
  },
  "ruta_id": "8399224e-90bc-5ee6-a282-8b58a249cecd",
  "ruteo_run_id": "4730febb-aa0c-4372-8918-04387d31fe71",
  "salida_base": "2026-10-01T09:00:00-06:00",
  "zona_horaria": "America/Mexico_City"
};

export const paradas: Respuesta<"/app/v1/ruteo/rutas/{ruta_id}/paradas"> = {
  "elementos": [
    {
      "cuenta_id": "41095c46-babd-53a1-a59b-231da3e9d99a",
      "distancia_desde_anterior_m": 1398,
      "duracion_desde_anterior_s": 203,
      "espera_s": 0,
      "estado": "PROGRAMADA",
      "fin_servicio": "2026-10-01T09:18:23-06:00",
      "gestor": "GESTOR_RUTA_002",
      "inicio_servicio": "2026-10-01T09:03:23-06:00",
      "llegada_estimada": "2026-10-01T09:03:23-06:00",
      "motivo": null,
      "orden_visita": 1,
      "prioridad": "MUY_ALTA",
      "ruta_id": "8399224e-90bc-5ee6-a282-8b58a249cecd",
      "salida_anterior": "2026-10-01T09:00:00-06:00",
      "snap_distancia_m": "60.04"
    },
    {
      "cuenta_id": "9e2792ce-0dfc-5231-9a5f-7901b92012cb",
      "distancia_desde_anterior_m": 0,
      "duracion_desde_anterior_s": 0,
      "espera_s": 0,
      "estado": "PROGRAMADA",
      "fin_servicio": "2026-10-01T09:33:23-06:00",
      "gestor": "GESTOR_RUTA_002",
      "inicio_servicio": "2026-10-01T09:18:23-06:00",
      "llegada_estimada": "2026-10-01T09:18:23-06:00",
      "motivo": null,
      "orden_visita": 2,
      "prioridad": "MUY_ALTA",
      "ruta_id": "8399224e-90bc-5ee6-a282-8b58a249cecd",
      "salida_anterior": "2026-10-01T09:18:23-06:00",
      "snap_distancia_m": "60.04"
    },
    {
      "cuenta_id": "0b8595e2-c0b0-54ca-9176-cc474fbfac26",
      "distancia_desde_anterior_m": 2899,
      "duracion_desde_anterior_s": 423,
      "espera_s": 4774,
      "estado": "PROGRAMADA",
      "fin_servicio": "2026-10-01T11:15:00-06:00",
      "gestor": "GESTOR_RUTA_002",
      "inicio_servicio": "2026-10-01T11:00:00-06:00",
      "llegada_estimada": "2026-10-01T09:40:26-06:00",
      "motivo": null,
      "orden_visita": 3,
      "prioridad": "MUY_ALTA",
      "ruta_id": "8399224e-90bc-5ee6-a282-8b58a249cecd",
      "salida_anterior": "2026-10-01T09:33:23-06:00",
      "snap_distancia_m": "9.85"
    },
    {
      "cuenta_id": "5242c682-6570-5f80-94d0-0f5f68bfb56c",
      "distancia_desde_anterior_m": 0,
      "duracion_desde_anterior_s": 0,
      "espera_s": 0,
      "estado": "PROGRAMADA",
      "fin_servicio": "2026-10-01T11:30:00-06:00",
      "gestor": "GESTOR_RUTA_002",
      "inicio_servicio": "2026-10-01T11:15:00-06:00",
      "llegada_estimada": "2026-10-01T11:15:00-06:00",
      "motivo": null,
      "orden_visita": 4,
      "prioridad": "MUY_ALTA",
      "ruta_id": "8399224e-90bc-5ee6-a282-8b58a249cecd",
      "salida_anterior": "2026-10-01T11:15:00-06:00",
      "snap_distancia_m": "9.85"
    },
    {
      "cuenta_id": "261eac0b-af0b-5572-8450-0dfc9d268d07",
      "distancia_desde_anterior_m": null,
      "duracion_desde_anterior_s": null,
      "espera_s": null,
      "estado": "NO_ENRUTABLE",
      "fin_servicio": null,
      "gestor": "GESTOR_RUTA_002",
      "inicio_servicio": null,
      "llegada_estimada": null,
      "motivo": "SNAP_EXCESIVO",
      "orden_visita": null,
      "prioridad": "MUY_ALTA",
      "ruta_id": "8399224e-90bc-5ee6-a282-8b58a249cecd",
      "salida_anterior": null,
      "snap_distancia_m": "795.43"
    },
    {
      "cuenta_id": "e2b28e01-6490-5607-afd5-a805b33491f3",
      "distancia_desde_anterior_m": null,
      "duracion_desde_anterior_s": null,
      "espera_s": null,
      "estado": "NO_ENRUTABLE",
      "fin_servicio": null,
      "gestor": "GESTOR_RUTA_002",
      "inicio_servicio": null,
      "llegada_estimada": null,
      "motivo": "SNAP_EXCESIVO",
      "orden_visita": null,
      "prioridad": "MUY_ALTA",
      "ruta_id": "8399224e-90bc-5ee6-a282-8b58a249cecd",
      "salida_anterior": null,
      "snap_distancia_m": "795.43"
    }
  ],
  "pagina": 1,
  "por_pagina": 50,
  "total": 6
};

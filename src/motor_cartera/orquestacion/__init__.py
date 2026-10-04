"""La orquestacion durable: quien ejecuta los motores, cuando y donde.

La API deja cada recurso EN_PROCESO junto con su trabajo en la cola, que es una tabla de la misma
PostgreSQL; un worker independiente lo toma, ejecuta su motor y encadena la etapa que sigue. Los
motores no saben nada de esto, y este paquete no sabe nada de sus reglas:

- `objetivos`: lo que la cola necesita saber de cada recurso que ejecuta un trabajo.
- `cola`: los trabajos, sus duenos, sus leases y sus intentos.
- `flujo`: la cadena ingesta, decision, territorial y ruteo de una corrida, y los trabajos que se
  piden a mano.
- `worker`: el proceso que toma los trabajos, los ejecuta con su latido y los cierra.

No reexporta nada: todo aqui usa la base, y cada modulo se importa por su nombre.
"""

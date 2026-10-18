import { ApiError } from "../api";

// Mensaje cuando una consulta nueva falla. Un 404 significa que el bot en ejecucion todavia no
// tiene la funcion (el frontend compilado se sirve en caliente, pero el backend solo cambia al
// reiniciarlo): se dice asi en vez de dejar la pestana rota. Cualquier otro error, generico.
export function Unavailable({ error, what }: { error: unknown; what: string }) {
  const notYet = error instanceof ApiError && error.status === 404;
  return (
    <p role="status" className="text-sm text-gray-500 dark:text-gray-400">
      {notYet
        ? `${what}: no disponible todavia. El bot en ejecucion aun no tiene esta funcion; hay que reiniciarlo con la Fase 5.`
        : `${what}: no disponible (error al consultar el servidor).`}
    </p>
  );
}

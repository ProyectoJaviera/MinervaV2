import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

// Dialogo de confirmacion explicita (Fase 5, controles del dashboard). Muestra la advertencia del
// servidor TAL CUAL (es la misma que Telegram) y solo habilita "Confirmar" cuando se escribio la
// palabra exacta. El servidor la vuelve a exigir: este dialogo es la primera defensa, no la unica.
export function ConfirmDialog({
  title,
  warning,
  phrase,
  confirmLabel,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  title: string;
  warning: string;
  phrase: string;
  confirmLabel: string;
  busy: boolean;
  error: string | null;
  onConfirm: (typed: string) => void;
  onCancel: () => void;
}) {
  const [typed, setTyped] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const warningId = useId();
  const inputId = useId();
  // Se compara sin importar mayusculas/espacios laterales, pero lo que viaja es la palabra exacta.
  const matches = typed.trim().toUpperCase() === phrase;

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape" && !busy) {
      event.stopPropagation();
      onCancel();
    }
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (matches && !busy) onConfirm(phrase);
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onKeyDown={handleKeyDown}
    >
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={warningId}
        onSubmit={handleSubmit}
        className="w-full max-w-md rounded-lg border border-gray-200 bg-white p-5 shadow-lg dark:border-gray-700 dark:bg-gray-800"
      >
        <h3 id={titleId} className="mb-2 text-base font-semibold text-gray-900 dark:text-gray-100">
          {title}
        </h3>
        <p
          id={warningId}
          className="mb-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-200"
        >
          {warning}
        </p>
        <label htmlFor={inputId} className="mb-1 block text-sm text-gray-700 dark:text-gray-300">
          Para confirmar escribe <strong>{phrase}</strong>
        </label>
        <input
          id={inputId}
          ref={inputRef}
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          autoComplete="off"
          spellCheck={false}
          maxLength={32}
          disabled={busy}
          className="mb-3 w-full rounded border border-gray-300 bg-white px-3 py-2 text-sm uppercase dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
        />
        {error && (
          <p role="alert" className="mb-3 text-sm text-red-700 dark:text-red-400">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
          >
            Cancelar
          </button>
          <button
            type="submit"
            disabled={!matches || busy}
            className="rounded bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-40"
          >
            {busy ? "Ejecutando..." : confirmLabel}
          </button>
        </div>
      </form>
    </div>
  );
}

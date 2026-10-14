import { useEffect, useState } from "react";
import { DashboardView } from "./DashboardView";
import { LoginView } from "./LoginView";
import { setUnauthorizedHandler, useMe } from "./api";

const THEME_KEY = "minerva-theme";

function readStoredTheme(): "light" | "dark" | null {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    return stored === "light" || stored === "dark" ? stored : null;
  } catch {
    return null;
  }
}

function useDarkMode(): [boolean, () => void] {
  const [dark, setDark] = useState<boolean>(() => {
    const stored = readStoredTheme();
    if (stored) return stored === "dark";
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  });

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  function toggle() {
    setDark((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(THEME_KEY, next ? "dark" : "light");
      } catch {
        // almacenamiento del navegador no disponible (privado/bloqueado) -- no es fatal
      }
      return next;
    });
  }

  return [dark, toggle];
}

export default function App() {
  const [dark, toggleDark] = useDarkMode();
  const [forcedLoggedOut, setForcedLoggedOut] = useState(false);
  const me = useMe();

  useEffect(() => {
    setUnauthorizedHandler(() => setForcedLoggedOut(true));
    return () => setUnauthorizedHandler(null);
  }, []);

  // Un login nuevo exitoso (tras un 401 anterior) debe limpiar el forzado --
  // si no, la sesion quedaria mostrando LoginView para siempre despues de
  // reingresar.
  useEffect(() => {
    if (me.data?.username) setForcedLoggedOut(false);
  }, [me.data?.username]);

  const loggedIn = !forcedLoggedOut && !!me.data?.username;

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <div className="bg-red-700 px-4 py-2 text-center text-sm font-bold text-white">
        MODO PAPER TRADING -- ninguna orden real se envia a Bitunix
      </div>
      <div className="flex justify-end px-4 pt-2">
        <button
          onClick={toggleDark}
          className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
        >
          {dark ? "Modo claro" : "Modo oscuro"}
        </button>
      </div>
      {me.isLoading ? (
        <p className="p-4 text-sm text-gray-500">Cargando...</p>
      ) : loggedIn && me.data ? (
        <DashboardView username={me.data.username} />
      ) : (
        <LoginView />
      )}
    </div>
  );
}

import { useState } from "react";
import { useLogin } from "./api";

export function LoginView() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const login = useLogin();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    login.mutate({ username, password });
  }

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-lg border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-700 dark:bg-gray-800"
      >
        <h1 className="mb-4 text-lg font-semibold text-gray-900 dark:text-gray-100">
          Minerva -- iniciar sesion
        </h1>
        <label className="mb-1 block text-sm text-gray-600 dark:text-gray-300" htmlFor="username">
          Usuario
        </label>
        <input
          id="username"
          autoComplete="username"
          className="mb-3 w-full rounded border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          maxLength={128}
        />
        <label className="mb-1 block text-sm text-gray-600 dark:text-gray-300" htmlFor="password">
          Contrasena
        </label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          className="mb-4 w-full rounded border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          maxLength={128}
        />
        <button
          type="submit"
          disabled={login.isPending}
          className="w-full rounded bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {login.isPending ? "Ingresando..." : "Ingresar"}
        </button>
        {login.isError && (
          <p className="mt-3 text-sm text-red-600 dark:text-red-400">
            Usuario o contrasena incorrectos.
          </p>
        )}
      </form>
    </div>
  );
}

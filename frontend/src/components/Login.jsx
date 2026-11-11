import { useState } from 'react'

// Todas las llamadas al backend pasan por src/api.js, que usa rutas relativas
// /api (Nginx hace el proxy al servicio "backend" dentro de Docker).
// El login solo elige el ID; los datos se cargan en App.
export default function Login({ onEntrar }) {
  const [id, setId] = useState('')
  const [error, setError] = useState(null)

  function enviar(evento) {
    evento.preventDefault()

    const numero = Number(id)
    if (!Number.isInteger(numero) || numero <= 0) {
      setError('Ingresa un ID de estudiante válido (número entero mayor que 0).')
      return
    }

    setError(null)
    onEntrar(numero)
  }

  return (
    <div className="mx-auto mt-12 max-w-md">
      <div className="rounded-xl bg-white p-6 shadow">
        <h2 className="text-lg font-semibold text-gray-800">Iniciar sesión</h2>
        <p className="mt-1 text-sm text-gray-500">
          Ingresa con tu ID de estudiante para consultar la oferta y generar tu
          horario.
        </p>

        <form onSubmit={enviar} className="mt-5 space-y-4" noValidate>
          <label className="block">
            <span className="text-sm font-medium text-gray-700">ID del estudiante</span>
            <input
              type="number"
              min="1"
              step="1"
              inputMode="numeric"
              autoFocus
              value={id}
              onChange={(evento) => {
                setId(evento.target.value)
                if (error) setError(null)
              }}
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200"
              placeholder="Ej: 42"
            />
          </label>

          {error && (
            <p
              role="alert"
              className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            className="w-full rounded-lg bg-indigo-600 py-2.5 font-semibold text-white shadow transition-colors hover:bg-indigo-500 active:bg-indigo-700"
          >
            Entrar
          </button>
        </form>

        <p className="mt-4 text-xs text-gray-400">
          La API se consume a través de <span className="font-mono">/api</span> (mismo
          origen; Nginx la reenvía al backend).
        </p>
      </div>
    </div>
  )
}

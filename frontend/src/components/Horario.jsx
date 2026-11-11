import Spinner from './Spinner.jsx'

const DIAS = {
  1: 'Lunes',
  2: 'Martes',
  3: 'Miércoles',
  4: 'Jueves',
  5: 'Viernes',
  6: 'Sábado',
  7: 'Domingo',
}

// Las llamadas al backend pasan por src/api.js (rutas relativas /api).
// (definido en src/api.js). Este componente es de presentación.

function formatearHora(valor) {
  if (valor == null || valor === '') return '—'
  return String(valor).slice(0, 5)
}

export default function Horario({ horario, cargando = false }) {
  const filas = Array.isArray(horario)
    ? horario.filter((fila) => fila && typeof fila === 'object')
    : []

  // Suma créditos una sola vez por NRC/curso, ya que cada fila es una sesión.
  const totalCreditos = (() => {
    const vistos = new Set()
    let total = 0
    for (const fila of filas) {
      const clave = fila.nrc_id ?? fila.codigo_alfanumerico ?? fila.materia
      if (vistos.has(clave)) continue
      vistos.add(clave)
      total += Number(fila.creditos) || 0
    }
    return total
  })()

  return (
    <section className="rounded-xl bg-white p-4 shadow">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold text-gray-800">Horario actual</h2>
        <span className="text-xs text-gray-500">
          {filas.length} {filas.length === 1 ? 'sesión' : 'sesiones'} ·{' '}
          {totalCreditos} créditos
        </span>
      </div>

      {cargando && filas.length === 0 ? (
        <Spinner label="Cargando horario…" className="h-4 w-4" />
      ) : filas.length === 0 ? (
        <p className="text-sm text-gray-500">
          Aún no tienes materias inscritas. Usa la oferta o genera tu horario
          automático.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] border-collapse text-sm">
            <thead>
              <tr className="border-b text-left text-xs uppercase tracking-wide text-gray-500">
                <th className="py-2 pr-3 font-semibold">Día</th>
                <th className="py-2 pr-3 font-semibold">Hora</th>
                <th className="py-2 pr-3 font-semibold">Materia</th>
                <th className="py-2 pr-3 font-semibold">NRC</th>
                <th className="py-2 font-semibold">Créditos</th>
              </tr>
            </thead>
            <tbody>
              {filas.map((fila, indice) => (
                <tr
                  key={`${fila.nrc_id ?? 'nrc'}-${fila.numero_sesion ?? indice}-${indice}`}
                  className="border-b last:border-0 hover:bg-gray-50"
                >
                  <td className="py-2 pr-3 font-medium text-indigo-700">
                    {DIAS[fila.dia] || fila.dia_nombre || fila.dia || '—'}
                  </td>
                  <td className="py-2 pr-3 text-gray-600">
                    {formatearHora(fila.hora_inicio)} – {formatearHora(fila.hora_fin)}
                  </td>
                  <td className="py-2 pr-3">
                    {fila.materia || '—'}
                    {fila.codigo_alfanumerico && (
                      <span className="text-gray-400">
                        {' '}
                        ({fila.codigo_alfanumerico})
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-3 font-mono text-xs">{fila.nrc_id ?? '—'}</td>
                  <td className="py-2">{fila.creditos ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

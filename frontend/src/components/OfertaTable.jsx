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

function formatearHorario(sesiones) {
  if (!Array.isArray(sesiones) || sesiones.length === 0) return 'Por definir'
  return sesiones
    .map((sesion) => {
      if (!sesion || typeof sesion !== 'object') return '—'
      return `${DIAS[sesion.dia] || sesion.dia_nombre || sesion.dia || '—'} ${formatearHora(
        sesion.hora_inicio,
      )}–${formatearHora(sesion.hora_fin)}`
    })
    .join(' · ')
}

export default function OfertaTable({
  oferta,
  onInscribir,
  inscribiendoNrc = null,
  cargando = false,
}) {
  const filas = Array.isArray(oferta)
    ? oferta.filter((nrc) => nrc && typeof nrc === 'object')
    : []
  const haySinCupo = filas.some((nrc) => !nrc.disponible)

  return (
    <section className="rounded-xl bg-white p-4 shadow">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold text-gray-800">Oferta académica</h2>
        <span className="text-xs text-gray-500">
          {filas.length} NRC {filas.length === 1 ? 'disponible' : 'disponibles'}
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[680px] border-collapse text-sm">
          <thead>
            <tr className="border-b text-left text-xs uppercase tracking-wide text-gray-500">
              <th className="py-2 pr-3 font-semibold">NRC</th>
              <th className="py-2 pr-3 font-semibold">Materia</th>
              <th className="py-2 pr-3 font-semibold">Créditos</th>
              <th className="py-2 pr-3 font-semibold">Horario</th>
              <th className="py-2 pr-3 font-semibold">Cupo</th>
              <th className="py-2 text-right font-semibold">Acción</th>
            </tr>
          </thead>
          <tbody>
            {cargando && filas.length === 0 ? (
              <tr>
                <td colSpan={6} className="py-6 text-center text-gray-500">
                  <Spinner label="Cargando oferta…" className="h-4 w-4" />
                </td>
              </tr>
            ) : filas.length === 0 ? (
              <tr>
                <td colSpan={6} className="py-6 text-center text-gray-500">
                  No hay NRC para mostrar. Verifica la conexión con el backend y
                  vuelve a intentarlo.
                </td>
              </tr>
            ) : (
              filas.map((nrc) => {
                const disponible = Boolean(nrc.disponible)
                const ocupado = inscribiendoNrc != null

                return (
                  <tr
                    key={nrc.id_nrc}
                    className="border-b last:border-0 hover:bg-gray-50"
                  >
                    <td className="py-2 pr-3 font-mono text-xs">{nrc.id_nrc}</td>
                    <td className="py-2 pr-3">
                      <span className="font-medium text-gray-800">
                        {nrc.materia || '—'}
                      </span>
                      <span className="text-gray-400"> ({nrc.curso_id || '—'})</span>
                    </td>
                    <td className="py-2 pr-3">{nrc.creditos ?? '—'}</td>
                    <td className="py-2 pr-3 text-xs text-gray-600">
                      {formatearHorario(nrc.horario)}
                    </td>
                    <td className="py-2 pr-3">
                      <span
                        className={
                          disponible ? 'text-gray-700' : 'font-medium text-red-600'
                        }
                      >
                        {nrc.inscritos ?? 0}/{nrc.cupo_max ?? '—'}
                      </span>
                    </td>
                    <td className="py-2 text-right">
                      <button
                        type="button"
                        onClick={() => onInscribir(nrc.id_nrc)}
                        disabled={!disponible || ocupado}
                        title={!disponible ? 'Sin cupo disponible' : undefined}
                        className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        {inscribiendoNrc === nrc.id_nrc ? 'Inscribiendo…' : 'Inscribir'}
                      </button>
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>

      {haySinCupo && (
        <p className="mt-3 text-xs text-gray-500">
          Los NRC sin cupo no se pueden inscribir.
        </p>
      )}
    </section>
  )
}

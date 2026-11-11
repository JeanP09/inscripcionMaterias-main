import { useState } from 'react'
import Banner from './Banner.jsx'
import Spinner from './Spinner.jsx'
import { apiPost } from '../api.js'

// Las llamadas al backend pasan por src/api.js (rutas relativas /api).

const DIAS = {
  1: 'Lunes',
  2: 'Martes',
  3: 'Miércoles',
  4: 'Jueves',
  5: 'Viernes',
  6: 'Sábado',
  7: 'Domingo',
}

// El endpoint devuelve { horario: [...], no_asignadas: [...] }; se aceptan
// también variantes por robustez.
function extraerHorario(datos) {
  if (Array.isArray(datos)) {
    return datos.filter((item) => item && typeof item === 'object')
  }
  if (!datos || typeof datos !== 'object') return []
  const claves = ['horario', 'materias', 'resultado', 'asignadas']
  for (const clave of claves) {
    if (Array.isArray(datos[clave])) {
      return datos[clave].filter((item) => item && typeof item === 'object')
    }
  }
  return []
}

function extraerNoAsignadas(datos) {
  if (!datos || typeof datos !== 'object' || Array.isArray(datos)) return []
  const claves = [
    'no_asignadas',
    'materias_no_asignadas',
    'noAsignadas',
    'no_asignados',
  ]
  for (const clave of claves) {
    if (Array.isArray(datos[clave])) return datos[clave]
  }
  return []
}

function valor(item, claves) {
  if (!item || typeof item !== 'object') return null
  for (const clave of claves) {
    if (item[clave] != null && item[clave] !== '') return item[clave]
  }
  return null
}

function formatearHora(valorHora) {
  if (valorHora == null || valorHora === '') return '—'
  return String(valorHora).slice(0, 5)
}

// Aplana las `sesiones` anidadas a una fila por sesión.
function construirFilas(horario) {
  const filas = []

  horario.forEach((item, indice) => {
    const materia =
      valor(item, ['materia', 'nombre', 'codigo', 'codigo_alfanumerico']) || '—'
    const nrc = valor(item, ['nrc', 'nrc_id', 'id_nrc'])
    const sesiones = Array.isArray(item.sesiones) ? item.sesiones : []

    if (sesiones.length === 0) {
      filas.push({
        key: `${indice}-0`,
        materia,
        dia: valor(item, ['dia', 'dia_nombre']),
        inicio: valor(item, ['hora_inicio', 'horaInicio']),
        fin: valor(item, ['hora_fin', 'horaFin']),
        creditos: valor(item, ['creditos']),
        nrc,
      })
      return
    }

    sesiones.forEach((sesion, indiceSesion) => {
      filas.push({
        key: `${indice}-${indiceSesion}`,
        materia,
        dia: valor(sesion, ['dia_nombre', 'dia']),
        inicio: valor(sesion, ['hora_inicio', 'horaInicio']),
        fin: valor(sesion, ['hora_fin', 'horaFin']),
        creditos: valor(item, ['creditos']),
        nrc: valor(sesion, ['nrc', 'nrc_id', 'id_nrc']) ?? nrc,
      })
    })
  })

  return filas
}

export default function GenerarHorario({
  estudianteId,
  onGenerado,
  matrizCargada = false,
  matrizCargando = false,
}) {
  // null = aún no se ha generado; [] = se generó pero no hay materias.
  const [horario, setHorario] = useState(null)
  const [noAsignadas, setNoAsignadas] = useState([])
  const [cargando, setCargando] = useState(false)
  const [error, setError] = useState(null)

  async function generar() {
    if (estudianteId == null || cargando || !matrizCargada) return

    setCargando(true)
    setError(null)

    let generado = false

    try {
      const datos = await apiPost('/api/horario/automatico', {
        estudiante_id: estudianteId,
      })

      if (datos == null) {
        throw new Error('El servidor no devolvió ningún horario. Inténtalo de nuevo.')
      }

      setHorario(extraerHorario(datos))
      setNoAsignadas(extraerNoAsignadas(datos))
      generado = true
    } catch (err) {
      setError(err.message || 'No se pudo generar el horario.')
    } finally {
      setCargando(false)
    }

    // Refresca el horario y la oferta del panel del estudiante (los NRC se
    // acaban de inscribir en el backend).
    if (generado && typeof onGenerado === 'function') {
      try {
        onGenerado()
      } catch {
        // Un fallo al refrescar no debe invalidar el horario ya generado.
      }
    }
  }

  const filas = horario ? construirFilas(horario) : []

  return (
    <section
      aria-labelledby="paso-horario-titulo"
      className="rounded-xl border-2 border-indigo-100 bg-white p-4 shadow sm:p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span
            aria-hidden="true"
            className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-base font-bold ${
              matrizCargada
                ? 'bg-indigo-600 text-white'
                : 'bg-gray-100 text-gray-400'
            }`}
          >
            2
          </span>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2
                id="paso-horario-titulo"
                className="text-lg font-semibold text-gray-800"
              >
                Generar horario automático
              </h2>
              <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-indigo-700">
                Paso 2
              </span>
            </div>
            <p className="mt-0.5 text-sm text-gray-500">
              Con tu matriz cargada, el sistema elige los NRC que no se crucen y
              respetan tus prerrequisitos.
            </p>
          </div>
        </div>

        {horario && (
          <span className="rounded-full bg-indigo-50 px-3 py-1 text-xs font-medium text-indigo-700">
            {horario.length}{' '}
            {horario.length === 1 ? 'materia asignada' : 'materias asignadas'}
          </span>
        )}
      </div>

      <button
        type="button"
        onClick={generar}
        disabled={cargando || estudianteId == null || !matrizCargada}
        aria-describedby={!matrizCargada ? 'horario-ayuda' : undefined}
        className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-indigo-600 px-6 py-4 text-lg font-semibold text-white shadow-lg transition-colors hover:bg-indigo-500 focus:outline-none focus-visible:ring-4 focus-visible:ring-indigo-200 active:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none"
      >
        {cargando && <Spinner label={null} className="h-5 w-5" />}
        {cargando ? 'Generando horario…' : 'Generar Horario Automático'}
      </button>

      {!matrizCargada && (
        <div
          id="horario-ayuda"
          className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
        >
          {matrizCargando ? (
            <Spinner
              label="Verificando tu matriz de estudio…"
              className="h-4 w-4"
            />
          ) : (
            <>
              <p className="font-semibold">Completa el Paso 1 para continuar.</p>
              <p className="mt-1">
                Primero carga tu matriz de estudio (arriba) para habilitar el
                horario automático.
              </p>
            </>
          )}
        </div>
      )}

      {matrizCargada && !cargando && (
        <p className="mt-2 text-xs text-gray-500">
          Se inscribirán automáticamente los NRC compatibles con tus materias
          aprobadas y con cupo disponible.
        </p>
      )}

      {error && (
        <div className="mt-4">
          <Banner tipo="error" onCerrar={() => setError(null)}>
            {error}
          </Banner>
        </div>
      )}

      {horario === null && !error && matrizCargada && !cargando && (
        <p className="mt-4 text-sm text-gray-500">
          Aún no tienes un horario generado. Presiona el botón para crear tu
          horario automático.
        </p>
      )}

      {horario !== null && horario.length === 0 && !error && (
        <p className="mt-4 text-sm text-gray-500">
          No se pudo asignar ninguna materia para tu horario.
        </p>
      )}

      {noAsignadas.length > 0 && (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          <p className="font-semibold">
            Materias no asignadas ({noAsignadas.length})
          </p>
          <ul className="mt-1 list-inside list-disc space-y-1">
            {noAsignadas.map((materia, indice) => {
              if (materia == null) return null
              if (typeof materia === 'string') {
                return <li key={`no-${indice}`}>{materia}</li>
              }

              const titulo = materia.materia || materia.codigo || `Materia ${indice + 1}`
              const codigo =
                materia.codigo && materia.materia ? ` (${materia.codigo})` : ''
              const pendientes = Array.isArray(materia.prerrequisitos_pendientes)
                ? materia.prerrequisitos_pendientes
                : []

              return (
                <li key={`no-${indice}`}>
                  <span className="font-medium">
                    {titulo}
                    {codigo}
                  </span>
                  {materia.motivo && (
                    <span className="text-amber-700"> — {materia.motivo}</span>
                  )}
                  {pendientes.length > 0 && (
                    <span className="text-amber-700">
                      {' '}
                      (pendientes: {pendientes.join(', ')})
                    </span>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {filas.length > 0 && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[640px] border-collapse text-sm">
            <thead>
              <tr className="border-b text-left text-xs uppercase tracking-wide text-gray-500">
                <th className="py-2 pr-3 font-semibold">Materia</th>
                <th className="py-2 pr-3 font-semibold">Día</th>
                <th className="py-2 pr-3 font-semibold">Hora inicio</th>
                <th className="py-2 pr-3 font-semibold">Hora fin</th>
                <th className="py-2 font-semibold">NRC</th>
              </tr>
            </thead>
            <tbody>
              {filas.map((fila) => (
                <tr key={fila.key} className="border-b last:border-0 hover:bg-gray-50">
                  <td className="py-2 pr-3 font-medium text-gray-800">{fila.materia}</td>
                  <td className="py-2 pr-3">{DIAS[fila.dia] || fila.dia || '—'}</td>
                  <td className="py-2 pr-3">{formatearHora(fila.inicio)}</td>
                  <td className="py-2 pr-3">{formatearHora(fila.fin)}</td>
                  <td className="py-2 font-mono text-xs">
                    {fila.nrc != null ? fila.nrc : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

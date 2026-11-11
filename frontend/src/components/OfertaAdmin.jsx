import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Banner from './Banner.jsx'
import Spinner from './Spinner.jsx'
import { apiGet, listaDeObjetos } from '../api.js'

// Tabla de la oferta académica para el administrador.
// Consume GET /api/oferta (rutas relativas a través de src/api.js) y se
// refresca sola cada vez que `refreshKey` cambia; App lo incrementa cuando
// SubirOferta termina de procesar un CSV.

const DIAS = {
  1: 'Lun',
  2: 'Mar',
  3: 'Mié',
  4: 'Jue',
  5: 'Vie',
  6: 'Sáb',
  7: 'Dom',
}

function aNumero(valor) {
  if (valor == null || valor === '') return null
  const numero = Number(valor)
  return Number.isFinite(numero) ? numero : null
}

function formatearHora(valor) {
  if (valor == null || valor === '') return '—'
  return String(valor).slice(0, 5)
}

function nombreDia(valor) {
  if (valor == null || valor === '') return '—'
  return DIAS[valor] || DIAS[String(valor)] || String(valor)
}

function sesionesDe(nrc) {
  return Array.isArray(nrc.horario)
    ? nrc.horario.filter((sesion) => sesion && typeof sesion === 'object')
    : []
}

function horaActual() {
  return new Date().toLocaleTimeString('es-CO', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export default function OfertaAdmin({ refreshKey = 0 }) {
  const [oferta, setOferta] = useState([])
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState(null)
  const [busqueda, setBusqueda] = useState('')
  const [actualizadoEn, setActualizadoEn] = useState(null)

  // Descarta respuestas viejas si se refresca varias veces seguidas.
  const solicitudRef = useRef(0)

  const cargar = useCallback(async () => {
    const solicitud = ++solicitudRef.current
    setCargando(true)
    setError(null)

    try {
      const datos = await apiGet('/api/oferta')
      if (solicitud !== solicitudRef.current) return
      setOferta(listaDeObjetos(datos))
      setActualizadoEn(horaActual())
    } catch (err) {
      if (solicitud !== solicitudRef.current) return
      setError(err.message || 'No se pudo cargar la oferta académica.')
    } finally {
      if (solicitud === solicitudRef.current) setCargando(false)
    }
  }, [])

  // Carga inicial y recarga automática tras una subida de CSV.
  useEffect(() => {
    cargar()
  }, [cargar, refreshKey])

  const filtradas = useMemo(() => {
    const consulta = busqueda.trim().toLowerCase()
    if (!consulta) return oferta

    return oferta.filter((nrc) =>
      [nrc.id_nrc, nrc.materia, nrc.curso_id, nrc.jornada, nrc.modalidad]
        .filter((valor) => valor != null)
        .some((valor) => String(valor).toLowerCase().includes(consulta)),
    )
  }, [oferta, busqueda])

  const totalInscritos = oferta.reduce(
    (total, nrc) => total + (aNumero(nrc.inscritos) ?? 0),
    0,
  )
  const totalCupos = oferta.reduce(
    (total, nrc) => total + (aNumero(nrc.cupo_max) ?? 0),
    0,
  )

  const botonReintentar = (
    <button
      type="button"
      onClick={cargar}
      disabled={cargando}
      className="rounded-md border border-red-300 bg-white px-3 py-1 text-xs font-semibold text-red-700 transition-colors hover:bg-red-50 disabled:opacity-50"
    >
      Reintentar
    </button>
  )

  return (
    <section
      aria-labelledby="oferta-admin-titulo"
      className="rounded-xl bg-white p-4 shadow sm:p-5"
    >
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2
              id="oferta-admin-titulo"
              className="text-lg font-semibold text-gray-800"
            >
              Oferta académica cargada
            </h2>
            {actualizadoEn && !cargando && (
              <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] text-gray-500">
                Actualizado {actualizadoEn}
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-gray-500">
            NRC registrados en el sistema: materia, jornada, horario y cupos.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-gray-400">GET /api/oferta</span>
          <button
            type="button"
            onClick={cargar}
            disabled={cargando}
            className="inline-flex items-center gap-2 rounded-md border border-indigo-200 bg-white px-3 py-1.5 text-sm font-medium text-indigo-700 transition-colors hover:bg-indigo-50 disabled:opacity-50"
          >
            {cargando && <Spinner label={null} className="h-3.5 w-3.5" />}
            {cargando ? 'Actualizando…' : 'Actualizar'}
          </button>
        </div>
      </div>

      {oferta.length > 0 && (
        <div className="mb-4 grid grid-cols-3 gap-2 sm:max-w-md">
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-center">
            <p className="text-lg font-bold text-gray-800">{oferta.length}</p>
            <p className="text-[11px] uppercase tracking-wide text-gray-500">NRC</p>
          </div>
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-center">
            <p className="text-lg font-bold text-gray-800">{totalCupos}</p>
            <p className="text-[11px] uppercase tracking-wide text-gray-500">Cupos</p>
          </div>
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-center">
            <p className="text-lg font-bold text-gray-800">{totalInscritos}</p>
            <p className="text-[11px] uppercase tracking-wide text-gray-500">
              Inscritos
            </p>
          </div>
        </div>
      )}

      {error && (
        <div className="mb-4">
          <Banner
            tipo="error"
            titulo="No se pudo cargar la oferta académica"
            accion={botonReintentar}
            onCerrar={() => setError(null)}
          >
            {error}
          </Banner>
        </div>
      )}

      {oferta.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <label className="relative w-full sm:max-w-xs">
            <span className="sr-only">Buscar en la oferta</span>
            <span
              aria-hidden="true"
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-gray-400"
            >
              🔍
            </span>
            <input
              type="search"
              value={busqueda}
              onChange={(evento) => setBusqueda(evento.target.value)}
              placeholder="Buscar por NRC, materia o jornada…"
              className="w-full rounded-lg border border-gray-300 py-2 pl-9 pr-3 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200"
            />
          </label>
          <span className="text-xs text-gray-500">
            {filtradas.length === oferta.length
              ? `${oferta.length} NRC`
              : `${filtradas.length} de ${oferta.length} NRC`}
          </span>
        </div>
      )}

      {cargando && oferta.length === 0 ? (
        <Spinner
          label="Cargando la oferta académica…"
          className="h-8 w-8"
          centrado
        />
      ) : oferta.length === 0 ? (
        error ? null : (
          <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-10 text-center">
            <p className="text-3xl" aria-hidden="true">
              📄
            </p>
            <p className="mt-2 font-medium text-gray-700">
              Todavía no hay oferta académica cargada.
            </p>
            <p className="mt-1 text-sm text-gray-500">
              Sube el CSV con «Subir Oferta Académica» y esta tabla se
              actualizará automáticamente.
            </p>
          </div>
        )
      ) : filtradas.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-8 text-center text-sm text-gray-500">
          Ningún NRC coincide con «{busqueda.trim()}».
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full min-w-[900px] border-collapse text-sm">
            <caption className="sr-only">
              Oferta académica con NRC, materia, créditos, jornada, horario y
              cupos.
            </caption>
            <thead className="bg-gray-50">
              <tr className="border-b border-gray-200 text-left text-[11px] uppercase tracking-wide text-gray-500">
                <th className="px-3 py-3 font-semibold">NRC</th>
                <th className="px-3 py-3 font-semibold">Materia</th>
                <th className="px-3 py-3 text-center font-semibold">Créditos</th>
                <th className="px-3 py-3 font-semibold">Jornada</th>
                <th className="px-3 py-3 font-semibold">Horario</th>
                <th className="px-3 py-3 font-semibold">
                  Cupos{' '}
                  <span className="font-normal normal-case text-gray-400">
                    (inscritos/máx.)
                  </span>
                </th>
              </tr>
            </thead>
            <tbody>
              {filtradas.map((nrc, indice) => {
                const sesiones = sesionesDe(nrc)
                const inscritos = aNumero(nrc.inscritos) ?? 0
                const cupoMax = aNumero(nrc.cupo_max)
                const disponibles =
                  cupoMax != null ? Math.max(cupoMax - inscritos, 0) : null
                const lleno =
                  nrc.disponible === false ||
                  (cupoMax != null && inscritos >= cupoMax)
                const porcentaje =
                  cupoMax != null && cupoMax > 0
                    ? Math.min(100, Math.round((inscritos / cupoMax) * 100))
                    : 0

                return (
                  <tr
                    key={`${nrc.id_nrc ?? 'nrc'}-${indice}`}
                    className="border-b border-gray-100 align-top last:border-0 hover:bg-indigo-50/30"
                  >
                    <td className="px-3 py-3 font-mono text-xs font-medium text-gray-700">
                      {nrc.id_nrc ?? '—'}
                    </td>
                    <td className="px-3 py-3">
                      <p className="font-medium text-gray-800">
                        {nrc.materia || '—'}
                      </p>
                      <p className="text-xs text-gray-400">
                        {nrc.curso_id || 'Sin código'}
                      </p>
                    </td>
                    <td className="px-3 py-3 text-center text-gray-700">
                      {nrc.creditos ?? '—'}
                    </td>
                    <td className="px-3 py-3">
                      <span className="inline-flex rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
                        {nrc.jornada || '—'}
                      </span>
                      {nrc.modalidad && (
                        <p className="mt-1 text-xs text-gray-400">{nrc.modalidad}</p>
                      )}
                    </td>
                    <td className="px-3 py-3">
                      {sesiones.length === 0 ? (
                        <span className="text-xs text-gray-400">Por definir</span>
                      ) : (
                        <div className="flex max-w-[330px] flex-wrap gap-1">
                          {sesiones.map((sesion, indiceSesion) => (
                            <span
                              key={`${nrc.id_nrc ?? indice}-s-${indiceSesion}`}
                              className="inline-flex items-center gap-1 rounded-md bg-indigo-50 px-2 py-1 text-xs text-indigo-700"
                            >
                              <span className="font-semibold">
                                {nombreDia(sesion.dia)}
                              </span>
                              <span>
                                {formatearHora(sesion.hora_inicio)}–
                                {formatearHora(sesion.hora_fin)}
                              </span>
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-3">
                      <div className="min-w-[130px]">
                        <div className="flex items-center justify-between gap-2 text-xs">
                          <span
                            className={
                              lleno
                                ? 'font-semibold text-red-600'
                                : 'font-medium text-gray-700'
                            }
                          >
                            {inscritos}/{cupoMax ?? '—'}
                          </span>
                          <span
                            className={
                              lleno
                                ? 'font-medium text-red-500'
                                : 'text-emerald-600'
                            }
                          >
                            {lleno
                              ? 'Sin cupo'
                              : disponibles != null
                                ? `${disponibles} libres`
                                : ''}
                          </span>
                        </div>
                        <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-gray-100">
                          <div
                            className={`h-full rounded-full ${
                              lleno ? 'bg-red-500' : 'bg-emerald-500'
                            }`}
                            style={{ width: `${porcentaje}%` }}
                          />
                        </div>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {oferta.length > 0 && (
        <p className="mt-3 text-xs text-gray-400">
          Los cupos muestran inscritos sobre el máximo. Los NRC sin cupo
          aparecen en rojo.
        </p>
      )}
    </section>
  )
}

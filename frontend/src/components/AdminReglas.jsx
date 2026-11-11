import { useCallback, useEffect, useMemo, useState } from 'react'
import Banner from './Banner.jsx'
import Spinner from './Spinner.jsx'
import { apiGet, apiPut } from '../api.js'

// Reglas de prerrequisitos (MongoDB) desde el panel de administrador.
// Se editan SIEMPRE sobre materias que ya existen en las reglas y se envía el
// arreglo completo por PUT /api/admin/reglas; nunca se crean materias nuevas.

const CLASE_SELECT =
  'mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-800 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200 disabled:cursor-not-allowed disabled:bg-gray-50 disabled:text-gray-400'

function texto(valor) {
  if (valor == null) return ''
  return String(valor).trim()
}

// Normaliza GET /api/admin/reglas conservando los metadatos opcionales
// (nombre, creditos, semestre) que usa el catálogo para mostrar cada tarjeta.
function normalizarReglas(datos) {
  if (!Array.isArray(datos)) return []

  const porMateria = new Map()

  datos.forEach((regla) => {
    if (!regla || typeof regla !== 'object' || Array.isArray(regla)) return

    const materia = texto(regla.materia).toUpperCase()
    if (!materia) return

    const previa = porMateria.get(materia)
    const prerequisitos = Array.isArray(regla.prerrequisitos)
      ? regla.prerrequisitos
          .map((prerequisito) => texto(prerequisito).toUpperCase())
          .filter(Boolean)
      : []

    porMateria.set(materia, {
      materia,
      prerrequisitos: [
        ...new Set([...(previa ? previa.prerrequisitos : []), ...prerequisitos]),
      ],
      nombre: texto(regla.nombre) || (previa ? previa.nombre : ''),
      creditos: regla.creditos ?? (previa ? previa.creditos : null),
      semestre: regla.semestre ?? (previa ? previa.semestre : null),
    })
  })

  return [...porMateria.values()].sort((a, b) =>
    a.materia.localeCompare(b.materia),
  )
}

// El contrato del PUT solo necesita {materia, prerrequisitos}; los metadatos
// del catálogo los conserva el backend.
function reglasParaEnviar(reglas) {
  return reglas.map((regla) => ({
    materia: regla.materia,
    prerrequisitos: [...regla.prerrequisitos],
  }))
}

export default function AdminReglas() {
  const [reglas, setReglas] = useState([])
  const [cargando, setCargando] = useState(true)
  const [guardando, setGuardando] = useState(false)
  const [errorCarga, setErrorCarga] = useState(null)
  const [mensaje, setMensaje] = useState(null)
  const [filtro, setFiltro] = useState('')

  // Formulario para agregar un prerrequisito.
  const [materia, setMateria] = useState('')
  const [prerequisito, setPrerequisito] = useState('')
  const [errorFormulario, setErrorFormulario] = useState(null)

  const cargar = useCallback(async ({ silencioso = false } = {}) => {
    if (!silencioso) setCargando(true)
    setErrorCarga(null)

    try {
      const datos = await apiGet('/api/admin/reglas')
      setReglas(normalizarReglas(datos))
      return true
    } catch (error) {
      setErrorCarga(
        error.message || 'No se pudieron cargar las reglas de prerrequisitos.',
      )
      return false
    } finally {
      if (!silencioso) setCargando(false)
    }
  }, [])

  useEffect(() => {
    cargar()
  }, [cargar])

  async function guardar(nuevasReglas, exito) {
    if (guardando) return

    setGuardando(true)
    setMensaje(null)
    setErrorFormulario(null)

    try {
      await apiPut('/api/admin/reglas', reglasParaEnviar(nuevasReglas))
      // Refleja el cambio de inmediato aunque la recarga posterior falle.
      setReglas(nuevasReglas)
      setMensaje({
        tipo: 'ok',
        texto: exito || 'Reglas actualizadas correctamente.',
      })
      setPrerequisito('')

      // Recarga silenciosa para reflejar exactamente lo que guardó el backend.
      const recargado = await cargar({ silencioso: true })
      if (!recargado) {
        setMensaje({
          tipo: 'aviso',
          texto:
            'Los cambios se guardaron, pero no se pudo refrescar la lista. Pulsa «Recargar».',
        })
      }
    } catch (error) {
      setMensaje({
        tipo: 'error',
        texto:
          error.message || 'No se pudieron guardar las reglas de prerrequisitos.',
      })
    } finally {
      setGuardando(false)
    }
  }

  function agregarPrerequisito(evento) {
    evento.preventDefault()

    // Nunca enviar el arreglo completo si aún no se cargó (o falló la carga):
    // se sobrescribirían las reglas del servidor con una lista incompleta.
    if (cargando || guardando || errorCarga || reglas.length === 0) return

    setMensaje(null)
    setErrorFormulario(null)

    const materiaNormalizada = materia.trim().toUpperCase()
    const prerequisitoNormalizado = prerequisito.trim().toUpperCase()

    if (!materiaNormalizada) {
      setErrorFormulario('Selecciona una materia existente de la lista.')
      return
    }

    if (!prerequisitoNormalizado) {
      setErrorFormulario('Selecciona el prerrequisito que quieres agregar.')
      return
    }

    if (materiaNormalizada === prerequisitoNormalizado) {
      setErrorFormulario('Una materia no puede ser prerrequisito de sí misma.')
      return
    }

    const existente = reglas.find((regla) => regla.materia === materiaNormalizada)

    // El administrador solo edita materias que ya vienen precargadas: nunca
    // se crea una materia nueva desde aquí.
    if (!existente) {
      setErrorFormulario(
        `La materia ${materiaNormalizada} ya no está en la lista. Recarga las reglas.`,
      )
      return
    }

    if (existente.prerrequisitos.includes(prerequisitoNormalizado)) {
      setErrorFormulario(
        `${prerequisitoNormalizado} ya es prerrequisito de ${materiaNormalizada}.`,
      )
      return
    }

    const nuevasReglas = reglas.map((regla) =>
      regla.materia === materiaNormalizada
        ? {
            ...regla,
            prerrequisitos: [
              ...regla.prerrequisitos,
              prerequisitoNormalizado,
            ],
          }
        : regla,
    )

    setFiltro('')
    guardar(
      nuevasReglas,
      `Prerrequisito ${prerequisitoNormalizado} agregado a ${materiaNormalizada}.`,
    )
  }

  function quitarPrerequisito(materiaObjetivo, prerequisitoObjetivo) {
    if (guardando || cargando || errorCarga) return

    const nuevasReglas = reglas.map((regla) =>
      regla.materia === materiaObjetivo
        ? {
            ...regla,
            prerrequisitos: regla.prerrequisitos.filter(
              (prerequisitoActual) => prerequisitoActual !== prerequisitoObjetivo,
            ),
          }
        : regla,
    )

    guardar(
      nuevasReglas,
      `Prerrequisito ${prerequisitoObjetivo} quitado de ${materiaObjetivo}.`,
    )
  }

  const materiasDisponibles = useMemo(
    () => reglas.map((regla) => regla.materia),
    [reglas],
  )
  const totalPrerequisitos = reglas.reduce(
    (total, regla) => total + regla.prerrequisitos.length,
    0,
  )
  const sinPrerequisitos = reglas.filter(
    (regla) => regla.prerrequisitos.length === 0,
  ).length

  // Solo se puede elegir una materia que ya existe en las reglas cargadas.
  const materiaSeleccionada = materiasDisponibles.includes(materia) ? materia : ''
  const reglaSeleccionada =
    reglas.find((regla) => regla.materia === materiaSeleccionada) || null
  const prerrequisitosActuales = reglaSeleccionada
    ? reglaSeleccionada.prerrequisitos
    : []
  const opcionesPrerequisito = materiasDisponibles.filter(
    (codigo) =>
      codigo !== materiaSeleccionada && !prerrequisitosActuales.includes(codigo),
  )

  const formularioDeshabilitado =
    cargando ||
    guardando ||
    Boolean(errorCarga) ||
    materiasDisponibles.length === 0
  const quitarDeshabilitado = formularioDeshabilitado

  const reglasFiltradas = useMemo(() => {
    const consulta = filtro.trim().toLowerCase()
    if (!consulta) return reglas

    return reglas.filter(
      (regla) =>
        regla.materia.toLowerCase().includes(consulta) ||
        texto(regla.nombre).toLowerCase().includes(consulta) ||
        regla.prerrequisitos.some((codigo) =>
          codigo.toLowerCase().includes(consulta),
        ),
    )
  }, [reglas, filtro])

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold text-gray-800">
            Reglas de prerrequisitos
          </h2>
          <p className="text-sm text-gray-500">
            Define qué materias exigen otras antes de poder inscribirlas. Los
            cambios se guardan como el arreglo completo de reglas.
          </p>
        </div>
        <button
          type="button"
          onClick={() => cargar()}
          disabled={cargando || guardando}
          className="inline-flex items-center gap-2 rounded-md border border-indigo-200 bg-white px-3 py-1.5 text-sm font-medium text-indigo-700 transition-colors hover:bg-indigo-50 disabled:opacity-50"
        >
          {cargando && <Spinner label={null} className="h-3.5 w-3.5" />}
          {cargando ? 'Actualizando…' : 'Recargar'}
        </button>
      </div>

      {mensaje && (
        <Banner tipo={mensaje.tipo} onCerrar={() => setMensaje(null)}>
          {mensaje.texto}
        </Banner>
      )}

      {/* Formulario: únicamente materias existentes en las reglas. */}
      <section className="rounded-xl border border-indigo-100 bg-white p-4 shadow-sm sm:p-5">
        <div className="flex items-start gap-3">
          <span
            aria-hidden="true"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-lg font-bold text-indigo-700"
          >
            ＋
          </span>
          <div>
            <h3 className="text-base font-semibold text-gray-800">
              Agregar prerrequisito
            </h3>
            <p className="text-sm text-gray-500">
              Elige una materia existente y el prerrequisito que debe exigir.
              No se pueden crear materias nuevas desde este panel.
            </p>
          </div>
        </div>

        {materiasDisponibles.length === 0 ? (
          cargando ? (
            <div className="mt-4">
              <Spinner
                label="Cargando materias para editar…"
                className="h-4 w-4"
              />
            </div>
          ) : errorCarga ? (
            <p className="mt-4 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500">
              El formulario se habilitará cuando las reglas se carguen
              correctamente.
            </p>
          ) : (
            <p className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
              No hay materias disponibles para editar. Carga la oferta académica
              o revisa las reglas en MongoDB.
            </p>
          )
        ) : (
          <form
            onSubmit={agregarPrerequisito}
            className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_auto] lg:items-end"
          >
            <label className="block">
              <span className="text-sm font-medium text-gray-700">
                Materia
              </span>
              <select
                value={materiaSeleccionada}
                onChange={(evento) => {
                  setMateria(evento.target.value)
                  setPrerequisito('')
                  setErrorFormulario(null)
                }}
                disabled={formularioDeshabilitado}
                className={CLASE_SELECT}
              >
                <option value="">Selecciona una materia…</option>
                {materiasDisponibles.map((codigo) => (
                  <option key={codigo} value={codigo}>
                    {codigo}
                  </option>
                ))}
              </select>
            </label>

            <label className="block">
              <span className="text-sm font-medium text-gray-700">
                Prerrequisito requerido
              </span>
              <select
                value={prerequisito}
                onChange={(evento) => {
                  setPrerequisito(evento.target.value)
                  setErrorFormulario(null)
                }}
                disabled={
                  formularioDeshabilitado ||
                  !materiaSeleccionada ||
                  opcionesPrerequisito.length === 0
                }
                className={CLASE_SELECT}
              >
                <option value="">
                  {materiaSeleccionada
                    ? 'Selecciona un prerrequisito…'
                    : 'Primero elige una materia'}
                </option>
                {opcionesPrerequisito.map((codigo) => (
                  <option key={codigo} value={codigo}>
                    {codigo}
                  </option>
                ))}
              </select>
            </label>

            <button
              type="submit"
              disabled={
                formularioDeshabilitado || !materiaSeleccionada || !prerequisito
              }
              className="inline-flex items-center justify-center gap-2 rounded-lg bg-indigo-600 px-5 py-2.5 font-semibold text-white shadow transition-colors hover:bg-indigo-500 active:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40 sm:col-span-2 lg:col-span-1"
            >
              {guardando && <Spinner label={null} className="h-4 w-4" />}
              {guardando ? 'Guardando…' : 'Agregar prerrequisito'}
            </button>

            {materiaSeleccionada && opcionesPrerequisito.length === 0 && (
              <p className="text-xs text-gray-500 sm:col-span-2 lg:col-span-3">
                No hay otras materias disponibles para agregar como
                prerrequisito a {materiaSeleccionada}.
              </p>
            )}

            {errorFormulario && (
              <div className="sm:col-span-2 lg:col-span-3">
                <Banner tipo="aviso" onCerrar={() => setErrorFormulario(null)}>
                  {errorFormulario}
                </Banner>
              </div>
            )}
          </form>
        )}
      </section>

      {/* Tarjetas por materia con chips de prerrequisitos y botón ×. */}
      <section className="rounded-xl bg-white p-4 shadow sm:p-5">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-lg font-semibold text-gray-800">
                Materias y prerrequisitos
              </h3>
              {!(cargando && reglas.length === 0) && (
                <>
                  <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700">
                    {reglas.length}{' '}
                    {reglas.length === 1 ? 'materia' : 'materias'}
                  </span>
                  <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600">
                    {totalPrerequisitos}{' '}
                    {totalPrerequisitos === 1
                      ? 'prerrequisito'
                      : 'prerrequisitos'}
                  </span>
                  {sinPrerequisitos > 0 && (
                    <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">
                      {sinPrerequisitos} sin prerrequisitos
                    </span>
                  )}
                </>
              )}
            </div>
            <p className="mt-1 text-sm text-gray-500">
              Quita un prerrequisito con «×»; el cambio se guarda al instante.
            </p>
          </div>

          {reglas.length > 0 && (
            <label className="relative w-full sm:max-w-xs">
              <span className="sr-only">Buscar materia o prerrequisito</span>
              <span
                aria-hidden="true"
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-gray-400"
              >
                🔍
              </span>
              <input
                type="search"
                value={filtro}
                onChange={(evento) => setFiltro(evento.target.value)}
                placeholder="Buscar materia o prerrequisito…"
                className="w-full rounded-lg border border-gray-300 py-2 pl-9 pr-3 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200"
              />
            </label>
          )}
        </div>

        {guardando && (
          <div className="mb-3">
            <Spinner label="Guardando cambios en las reglas…" className="h-4 w-4" />
          </div>
        )}

        {errorCarga && (
          <div className="mb-3">
            <Banner
              tipo="error"
              titulo="No se pudieron cargar las reglas"
              accion={
                <button
                  type="button"
                  onClick={() => cargar()}
                  disabled={cargando}
                  className="rounded-md border border-red-300 bg-white px-3 py-1 text-xs font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50"
                >
                  Reintentar
                </button>
              }
            >
              {errorCarga}
            </Banner>
          </div>
        )}

        {cargando && reglas.length === 0 ? (
          <Spinner
            label="Cargando reglas de prerrequisitos…"
            className="h-8 w-8"
            centrado
          />
        ) : reglas.length === 0 ? (
          errorCarga ? null : (
            <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-10 text-center">
              <p className="text-3xl" aria-hidden="true">
                🗂️
              </p>
              <p className="mt-2 font-medium text-gray-700">
                No hay materias precargadas en las reglas.
              </p>
              <p className="mt-1 text-sm text-gray-500">
                Carga la oferta académica o revisa las reglas en MongoDB.
              </p>
            </div>
          )
        ) : reglasFiltradas.length === 0 ? (
          <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-8 text-center text-sm text-gray-500">
            Ninguna materia coincide con «{filtro.trim()}».
          </div>
        ) : (
          <ul className="grid gap-3 lg:grid-cols-2">
            {reglasFiltradas.map((regla) => (
              <li
                key={regla.materia}
                className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm transition-colors hover:border-indigo-200"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="font-mono text-sm font-bold text-indigo-700">
                      {regla.materia}
                    </p>
                    {regla.nombre && (
                      <p
                        className="truncate text-sm text-gray-700"
                        title={regla.nombre}
                      >
                        {regla.nombre}
                      </p>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-1">
                    {regla.creditos != null && (
                      <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-600">
                        {regla.creditos} créd.
                      </span>
                    )}
                    {regla.semestre != null && (
                      <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-600">
                        Sem. {regla.semestre}
                      </span>
                    )}
                    <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] font-semibold text-indigo-700">
                      {regla.prerrequisitos.length}{' '}
                      {regla.prerrequisitos.length === 1 ? 'prereq.' : 'prereqs.'}
                    </span>
                  </div>
                </div>

                <div className="mt-3 border-t border-gray-100 pt-3">
                  {regla.prerrequisitos.length === 0 ? (
                    <p className="text-xs text-gray-400">Sin prerrequisitos</p>
                  ) : (
                    <div className="flex flex-wrap gap-1.5">
                      {regla.prerrequisitos.map((codigo) => (
                        <span
                          key={`${regla.materia}-${codigo}`}
                          className="inline-flex items-center gap-1 rounded-full border border-indigo-100 bg-indigo-50 py-1 pl-2.5 pr-1 text-xs font-medium text-indigo-800"
                        >
                          {codigo}
                          <button
                            type="button"
                            onClick={() =>
                              quitarPrerequisito(regla.materia, codigo)
                            }
                            disabled={quitarDeshabilitado}
                            title={`Quitar ${codigo} de ${regla.materia}`}
                            aria-label={`Quitar ${codigo} de ${regla.materia}`}
                            className="flex h-4 w-4 items-center justify-center rounded-full text-sm font-bold leading-none text-indigo-400 transition-colors hover:bg-red-100 hover:text-red-600 disabled:opacity-40"
                          >
                            ×
                          </button>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}

        {reglas.length > 0 && (
          <p className="mt-4 text-xs text-gray-400">
            PUT /api/admin/reglas · se envía el arreglo completo de reglas en
            cada cambio.
          </p>
        )}
      </section>
    </div>
  )
}

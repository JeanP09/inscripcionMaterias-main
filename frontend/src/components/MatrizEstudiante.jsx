import { useRef, useState } from 'react'
import Banner from './Banner.jsx'
import Spinner from './Spinner.jsx'
import { apiUpload } from '../api.js'

// Carga de la matriz de estudio (materias aprobadas) del estudiante.
// Todo pasa por src/api.js y usa rutas relativas /api.

const CLASE_INPUT =
  'mt-1 block w-full cursor-pointer rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200 disabled:cursor-not-allowed disabled:opacity-60'

function esObjeto(valor) {
  return Boolean(valor) && typeof valor === 'object' && !Array.isArray(valor)
}

function texto(valor) {
  if (valor == null) return ''
  return String(valor).trim()
}

function aEntero(valor) {
  if (typeof valor === 'number' && Number.isFinite(valor)) return Math.trunc(valor)
  if (typeof valor === 'string' && valor.trim() !== '') {
    const numero = Number(valor)
    if (Number.isFinite(numero)) return Math.trunc(numero)
  }
  return null
}

function normalizarMateria(item) {
  if (typeof item === 'string') {
    const codigo = item.trim()
    return codigo ? { codigo, nombre: '' } : null
  }

  if (!esObjeto(item)) return null

  const codigo = texto(
    item.codigo ??
      item.codigo_curso ??
      item.codigo_materia ??
      item.codigo_alfanumerico ??
      item.materia,
  )
  const nombre = texto(
    item.nombre ?? item.nombre_curso ?? item.nombre_materia ?? item.materia_nombre,
  )

  if (!codigo && !nombre) return null

  return { codigo: codigo || nombre, nombre }
}

function normalizarLista(valor) {
  if (!Array.isArray(valor)) return []

  const vistos = new Set()
  const materias = []

  valor.forEach((item) => {
    const materia = normalizarMateria(item)
    if (!materia) return

    const clave = materia.codigo.toUpperCase()
    if (vistos.has(clave)) return

    vistos.add(clave)
    materias.push(materia)
  })

  return materias
}

// El backend puede devolver la matriz en la raíz o anidada en `matriz`,
// `resumen`, `resultado` o `datos`; se combinan sin pisar la raíz.
function extraerCuerpo(datos) {
  if (!esObjeto(datos)) return {}

  const base = { ...datos }

  if (Array.isArray(datos.matriz)) {
    base.materias_aprobadas = datos.matriz
  }

  ;[datos.matriz, datos.resumen, datos.resultado, datos.datos]
    .filter(esObjeto)
    .forEach((contenedor) => {
      Object.entries(contenedor).forEach(([clave, valor]) => {
        if (base[clave] == null) base[clave] = valor
      })
    })

  return base
}

/**
 * Normaliza la respuesta de GET/POST de la matriz a la forma que usa la UI:
 * { carrera, semestre, aprobadas, porVer, totalAprobadas, totalPorVer }.
 * Devuelve null si la respuesta no contiene información de matriz.
 */
export function normalizarMatriz(datos) {
  if (!esObjeto(datos)) return null

  const base = extraerCuerpo(datos)

  const aprobadas = normalizarLista(
    base.materias_aprobadas ?? base.aprobadas ?? base.historial ?? [],
  )
  const porVer = normalizarLista(
    base.materias_por_ver ??
      base.por_ver ??
      base.materias_pendientes ??
      base.pendientes ??
      base.materias_por_cursar ??
      [],
  )

  const carrera = texto(base.carrera ?? base.programa ?? base.nombre_carrera)
  const semestre = texto(
    base.semestre ?? base.semestre_actual ?? base.semestreActual,
  )

  const totalAprobadas =
    aEntero(base.total_aprobadas ?? base.cantidad_aprobadas) ?? aprobadas.length
  const totalPorVer =
    aEntero(base.total_por_ver ?? base.cantidad_por_ver ?? base.total_pendientes) ??
    porVer.length

  const hayDatos =
    Boolean(carrera) ||
    Boolean(semestre) ||
    aprobadas.length > 0 ||
    porVer.length > 0 ||
    totalAprobadas > 0 ||
    totalPorVer > 0

  if (!hayDatos) return null

  return {
    carrera,
    semestre,
    aprobadas,
    porVer,
    totalAprobadas,
    totalPorVer,
  }
}

function mensajeDe(datos) {
  if (!esObjeto(datos)) return ''
  const mensaje = datos.mensaje ?? datos.message ?? datos.detail
  return typeof mensaje === 'string' ? mensaje.trim() : ''
}

function Chip({ materia, tono }) {
  const estilos =
    tono === 'aprobada'
      ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
      : 'border-amber-200 bg-amber-50 text-amber-800'

  return (
    <span
      title={materia.nombre || undefined}
      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium ${estilos}`}
    >
      {materia.codigo}
    </span>
  )
}

function TarjetaResumen({ etiqueta, valor, detalle, acento = 'indigo' }) {
  const estilos =
    acento === 'emerald'
      ? 'border-emerald-100 bg-emerald-50 text-emerald-700'
      : acento === 'amber'
        ? 'border-amber-100 bg-amber-50 text-amber-700'
        : 'border-indigo-100 bg-indigo-50 text-indigo-700'

  return (
    <div className={`rounded-lg border px-3 py-3 ${estilos}`}>
      <p className="text-[11px] font-medium uppercase tracking-wide opacity-80">
        {etiqueta}
      </p>
      <p className="mt-1 text-xl font-bold leading-tight text-gray-800 break-words">
        {valor}
      </p>
      {detalle && <p className="mt-0.5 text-xs text-gray-500">{detalle}</p>}
    </div>
  )
}

export default function MatrizEstudiante({
  estudianteId,
  matriz,
  cargando = false,
  errorCarga = null,
  onMatrizCargada,
  onRecargar,
}) {
  const inputRef = useRef(null)
  const [archivo, setArchivo] = useState(null)
  const [subiendo, setSubiendo] = useState(false)
  const [error, setError] = useState(null)
  const [mensajeOk, setMensajeOk] = useState(null)

  const resumen = normalizarMatriz(matriz)
  const cargada = resumen != null
  const estado = cargando ? 'Consultando…' : cargada ? 'Completado' : 'Pendiente'

  function seleccionarArchivo(evento) {
    setArchivo(evento.target.files && evento.target.files[0] ? evento.target.files[0] : null)
    setError(null)
    setMensajeOk(null)
  }

  async function subir(evento) {
    evento.preventDefault()

    if (subiendo) return

    setError(null)
    setMensajeOk(null)

    if (estudianteId == null) {
      setError('Inicia sesión para cargar tu matriz de estudio.')
      return
    }

    if (!archivo) {
      setError('Selecciona el archivo CSV de tu matriz de estudio.')
      return
    }

    if (!archivo.name.toLowerCase().endsWith('.csv')) {
      setError('El archivo debe tener extensión .csv.')
      return
    }

    if (archivo.size === 0) {
      setError('El archivo está vacío. Selecciona un CSV con datos.')
      return
    }

    const formData = new FormData()
    formData.append('archivo', archivo)
    formData.append('estudiante_id', String(estudianteId))

    setSubiendo(true)

    try {
      const datos = await apiUpload('/api/estudiante/upload-matriz', formData)
      const normalizado = normalizarMatriz(datos)

      setMensajeOk(
        mensajeDe(datos) || 'Matriz de estudio cargada correctamente.',
      )

      if (typeof onMatrizCargada === 'function') {
        // Si el POST no trae el detalle, se guarda un objeto marcador y se
        // consulta GET para obtener el resumen completo.
        onMatrizCargada(datos ?? { cargada: true })
      }

      setArchivo(null)
      if (inputRef.current) inputRef.current.value = ''

      if (!normalizado && typeof onRecargar === 'function') {
        onRecargar()
      }
    } catch (err) {
      setError(err.message || 'No se pudo cargar la matriz de estudio.')
    } finally {
      setSubiendo(false)
    }
  }

  const botonReintentar = (
    <button
      type="button"
      onClick={onRecargar}
      disabled={cargando}
      className="rounded-md border border-red-300 bg-white px-3 py-1 text-xs font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50"
    >
      Reintentar
    </button>
  )

  const formulario = (
    <form onSubmit={subir} className="space-y-3">
      <label className="block">
        <span className="text-sm font-medium text-gray-700">
          {cargada ? 'Nuevo archivo CSV' : 'Archivo CSV'}
        </span>
        <input
          ref={inputRef}
          type="file"
          accept=".csv"
          onChange={seleccionarArchivo}
          disabled={subiendo}
          className={CLASE_INPUT}
        />
      </label>

      {archivo && !subiendo && (
        <p className="text-xs text-gray-500">
          Archivo seleccionado:{' '}
          <span className="font-medium text-gray-700">{archivo.name}</span> (
          {Math.max(1, Math.round(archivo.size / 1024))} KB)
        </p>
      )}

      {subiendo && (
        <p role="status" className="text-sm font-medium text-indigo-700">
          Subiendo y procesando tu matriz…
        </p>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={subiendo || !archivo}
          className="inline-flex items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white shadow transition-colors hover:bg-indigo-500 active:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {subiendo && <Spinner label={null} className="h-4 w-4" />}
          {subiendo
            ? 'Subiendo…'
            : cargada
              ? 'Reemplazar matriz'
              : 'Subir matriz'}
        </button>
        <span className="text-xs text-gray-400">
          El navegador envía multipart/form-data (campos «archivo» y
          «estudiante_id»).
        </span>
      </div>
    </form>
  )

  return (
    <section
      aria-labelledby="paso-matriz-titulo"
      className={`rounded-xl border-2 bg-white p-4 shadow sm:p-5 ${
        cargada ? 'border-emerald-200' : 'border-indigo-200'
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span
            aria-hidden="true"
            className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-base font-bold ${
              cargada
                ? 'bg-emerald-100 text-emerald-700'
                : 'bg-indigo-600 text-white'
            }`}
          >
            {cargada ? '✓' : '1'}
          </span>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2
                id="paso-matriz-titulo"
                className="text-lg font-semibold text-gray-800"
              >
                Matriz de estudio
              </h2>
              <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-indigo-700">
                Paso 1 · Obligatorio
              </span>
            </div>
            <p className="mt-0.5 text-sm text-gray-500">
              {cargada
                ? 'Tu matriz está cargada. Ya puedes generar el horario automático.'
                : 'Sube el CSV con las materias aprobadas y por ver para habilitar el horario automático.'}
            </p>
          </div>
        </div>

        <span
          className={`rounded-full px-3 py-1 text-xs font-semibold ${
            cargada
              ? 'bg-emerald-100 text-emerald-800'
              : cargando
                ? 'bg-indigo-100 text-indigo-800'
                : 'bg-amber-100 text-amber-800'
          }`}
        >
          {estado}
        </span>
      </div>

      {errorCarga && (
        <div className="mt-4">
          <Banner
            tipo="error"
            titulo="No se pudo consultar tu matriz"
            accion={botonReintentar}
          >
            {errorCarga}
          </Banner>
        </div>
      )}

      {error && (
        <div className="mt-4">
          <Banner tipo="error" onCerrar={() => setError(null)}>
            {error}
          </Banner>
        </div>
      )}

      {mensajeOk && (
        <div className="mt-4">
          <Banner tipo="ok" onCerrar={() => setMensajeOk(null)}>
            {mensajeOk}
          </Banner>
        </div>
      )}

      {cargando && (
        <div className="mt-4 rounded-lg border border-indigo-100 bg-indigo-50/60 px-3 py-3">
          <Spinner label="Consultando tu matriz de estudio…" className="h-4 w-4" />
        </div>
      )}

      {!cargando && !cargada && !errorCarga && (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <p className="font-semibold">Aún no has cargado tu matriz.</p>
          <p className="mt-1">
            Este paso es obligatorio: el botón para generar el horario (Paso 2)
            permanecerá bloqueado hasta que subas el archivo.
          </p>
        </div>
      )}

      {cargada ? (
        <>
          <div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <TarjetaResumen
              etiqueta="Carrera"
              valor={resumen.carrera || 'No registrada'}
              detalle={resumen.carrera ? undefined : 'Sin dato en la matriz'}
            />
            <TarjetaResumen
              etiqueta="Semestre"
              valor={resumen.semestre || '—'}
              detalle={resumen.semestre ? undefined : 'Sin dato en la matriz'}
            />
            <TarjetaResumen
              etiqueta="Materias aprobadas"
              valor={resumen.totalAprobadas}
              detalle="Base para validar prerrequisitos"
              acento="emerald"
            />
            <TarjetaResumen
              etiqueta="Materias por ver"
              valor={resumen.totalPorVer}
              detalle="Pendientes por cursar"
              acento="amber"
            />
          </div>

          <div className="mt-4 rounded-lg border border-indigo-100 bg-indigo-50/50 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold text-gray-800">
                Materias aprobadas ({resumen.totalAprobadas})
              </h3>
            </div>
            {resumen.aprobadas.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-2">
                {resumen.aprobadas.map((materia) => (
                  <Chip
                    key={`aprobada-${materia.codigo}`}
                    materia={materia}
                    tono="aprobada"
                  />
                ))}
              </div>
            ) : (
              <p className="mt-1 text-xs text-gray-500">
                No se registran materias aprobadas en la matriz.
              </p>
            )}

            <div className="mt-4 border-t border-indigo-100 pt-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-semibold text-gray-800">
                  Materias por ver ({resumen.totalPorVer})
                </h3>
              </div>
              {resumen.porVer.length > 0 ? (
                <div className="mt-2 flex flex-wrap gap-2">
                  {resumen.porVer.map((materia) => (
                    <Chip
                      key={`por-ver-${materia.codigo}`}
                      materia={materia}
                      tono="por-ver"
                    />
                  ))}
                </div>
              ) : (
                <p className="mt-1 text-xs text-gray-500">
                  No hay materias pendientes por ver.
                </p>
              )}
            </div>
          </div>

          <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-4">
            <p className="text-sm font-medium text-gray-700">
              ¿Necesitas actualizar tu matriz?
            </p>
            <p className="mb-3 text-xs text-gray-500">
              Vuelve a subir el CSV y se reemplazarán las materias aprobadas y
              por ver de este estudiante.
            </p>
            {formulario}
          </div>
        </>
      ) : (
        <div className="mt-4">{formulario}</div>
      )}
    </section>
  )
}

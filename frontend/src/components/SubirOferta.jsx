import { useRef, useState } from 'react'
import Banner from './Banner.jsx'
import Spinner from './Spinner.jsx'
import { apiUpload } from '../api.js'

// Subida de la oferta académica (CSV) por parte del administrador.
// Todo pasa por src/api.js y usa rutas relativas /api.

const CLASE_INPUT =
  'mt-1 block w-full cursor-pointer rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-700 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200 disabled:cursor-not-allowed disabled:opacity-60'

const CLAVES_RESUMEN = {
  cursos: [
    'cursos',
    'total_cursos',
    'cursos_insertados',
    'cursos_procesados',
    'cursos_cargados',
    'totalCursos',
  ],
  nrcs: [
    'nrcs',
    'total_nrcs',
    'nrcs_insertados',
    'nrcs_procesados',
    'nrcs_cargados',
    'totalNrcs',
  ],
  sesiones: [
    'sesiones',
    'total_sesiones',
    'sesiones_insertadas',
    'sesiones_procesadas',
    'sesiones_cargadas',
    'totalSesiones',
  ],
}

function esObjeto(valor) {
  return Boolean(valor) && typeof valor === 'object' && !Array.isArray(valor)
}

// Acepta números, cadenas numéricas o arreglos (en ese caso usa su longitud).
function aCantidad(valor) {
  if (Array.isArray(valor)) return valor.length
  if (typeof valor === 'number' && Number.isFinite(valor)) return valor
  if (typeof valor === 'string' && valor.trim() !== '') {
    const numero = Number(valor)
    if (Number.isFinite(numero)) return numero
  }
  return null
}

/**
 * El backend puede responder con los conteos en la raíz o dentro de `resumen`,
 * `resultado` o `estadisticas`. Se aceptan todas las variantes conocidas.
 */
function extraerResumen(datos) {
  const fuentes = [
    datos,
    esObjeto(datos) ? datos.resumen : null,
    esObjeto(datos) ? datos.resultado : null,
    esObjeto(datos) ? datos.estadisticas : null,
  ].filter(esObjeto)

  const resumen = {}

  Object.entries(CLAVES_RESUMEN).forEach(([nombre, claves]) => {
    resumen[nombre] = null

    for (const fuente of fuentes) {
      for (const clave of claves) {
        const cantidad = aCantidad(fuente[clave])
        if (cantidad != null) {
          resumen[nombre] = cantidad
          return
        }
      }
    }
  })

  return resumen
}

function mensajeDe(datos) {
  if (!esObjeto(datos)) return ''
  const mensaje = datos.mensaje ?? datos.message ?? datos.detail
  return typeof mensaje === 'string' ? mensaje.trim() : ''
}

function Estadistica({ etiqueta, valor }) {
  return (
    <div className="rounded-lg border border-green-200 bg-white px-3 py-2 text-center">
      <p className="text-lg font-bold text-green-700">
        {valor != null ? valor : '—'}
      </p>
      <p className="text-xs uppercase tracking-wide text-gray-500">{etiqueta}</p>
    </div>
  )
}

export default function SubirOferta({ onSubida }) {
  const inputRef = useRef(null)
  const [archivo, setArchivo] = useState(null)
  const [subiendo, setSubiendo] = useState(false)
  const [error, setError] = useState(null)
  const [mensajeOk, setMensajeOk] = useState(null)
  const [resumen, setResumen] = useState(null)

  function seleccionarArchivo(evento) {
    setArchivo(evento.target.files && evento.target.files[0] ? evento.target.files[0] : null)
    setError(null)
    setMensajeOk(null)
    setResumen(null)
  }

  async function subir(evento) {
    evento.preventDefault()

    if (subiendo) return

    setError(null)
    setMensajeOk(null)
    setResumen(null)

    if (!archivo) {
      setError('Selecciona el archivo CSV de la oferta académica.')
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

    setSubiendo(true)

    try {
      const datos = await apiUpload('/api/admin/upload-oferta', formData)
      const conteos = extraerResumen(datos)

      setResumen(conteos)
      setMensajeOk(
        mensajeDe(datos) || 'Oferta académica cargada correctamente.',
      )

      setArchivo(null)
      if (inputRef.current) inputRef.current.value = ''

      if (typeof onSubida === 'function') {
        try {
          onSubida(datos)
        } catch {
          // Un fallo al refrescar no invalida una subida ya exitosa.
        }
      }
    } catch (err) {
      setError(err.message || 'No se pudo subir la oferta académica.')
    } finally {
      setSubiendo(false)
    }
  }

  const hayConteos =
    resumen != null &&
    (resumen.cursos != null || resumen.nrcs != null || resumen.sesiones != null)

  return (
    <section className="rounded-xl bg-white p-4 shadow">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold text-gray-800">
            Subir Oferta Académica
          </h2>
          <p className="text-sm text-gray-500">
            Carga el CSV con los cursos, NRC y sesiones de la oferta vigente.
          </p>
        </div>
        <span className="text-xs text-gray-400">POST /api/admin/upload-oferta</span>
      </div>

      {error && (
        <div className="mb-3">
          <Banner tipo="error" onCerrar={() => setError(null)}>
            {error}
          </Banner>
        </div>
      )}

      {mensajeOk && (
        <div className="mb-3">
          <Banner tipo="ok" onCerrar={() => setMensajeOk(null)}>
            {mensajeOk}
          </Banner>
        </div>
      )}

      {hayConteos && (
        <div className="mb-3">
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-gray-500">
            Resultado de la carga
          </p>
          <div className="grid grid-cols-3 gap-2">
            <Estadistica etiqueta="Cursos" valor={resumen.cursos} />
            <Estadistica etiqueta="NRC" valor={resumen.nrcs} />
            <Estadistica etiqueta="Sesiones" valor={resumen.sesiones} />
          </div>
        </div>
      )}

      <form onSubmit={subir} className="space-y-3">
        <label className="block">
          <span className="text-sm font-medium text-gray-700">Archivo CSV</span>
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
          <div
            role="status"
            className="rounded-lg border border-indigo-100 bg-indigo-50 px-3 py-2"
          >
            <Spinner
              label="Subiendo y procesando la oferta… esto puede tardar unos segundos."
              className="h-4 w-4"
            />
          </div>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={subiendo || !archivo}
            aria-busy={subiendo}
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 font-semibold text-white shadow transition-colors hover:bg-indigo-500 active:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {subiendo && <Spinner label={null} className="h-4 w-4" />}
            {subiendo ? 'Subiendo…' : 'Subir oferta'}
          </button>
          <span className="text-xs text-gray-400">
            El navegador envía multipart/form-data (campo «archivo»).
          </span>
        </div>
      </form>
    </section>
  )
}

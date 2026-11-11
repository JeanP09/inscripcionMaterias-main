// ---------------------------------------------------------------------------
// Cliente HTTP único de la SPA.
//
// Todas las llamadas al backend pasan por aquí, de modo que:
//   * la URL base está definida en un solo lugar y es configurable;
//   * por defecto se usan RUTAS RELATIVAS ("/api/..."): en Docker el navegador
//     llama al mismo origen del frontend y Nginx hace proxy hacia el servicio
//     interno "backend"; así no importa desde qué host se abra la app ni hay
//     problemas de CORS;
//   * se puede sobreescribir con la variable de entorno VITE_API_URL (se
//     incrusta en el build). En desarrollo, Vite también proxya /api.
//   * cualquier fallo de red (backend caído, CORS, DNS, timeout) se convierte
//     en un ApiError con un mensaje amigable en español;
//   * las respuestas no-ok también se convierten en ApiError (con el `detail`
//     que devuelve FastAPI cuando existe);
//   * nunca se hace `.json()` sobre una respuesta que no sea JSON.
// ---------------------------------------------------------------------------

const CONFIGURADA = import.meta.env.VITE_API_URL

// '' => rutas relativas al mismo origen (recomendado con Nginx/Docker).
export const API_BASE =
  CONFIGURADA === undefined || CONFIGURADA === null
    ? ''
    : String(CONFIGURADA).replace(/\/+$/, '')

export const MENSAJE_CONEXION = API_BASE
  ? `No se pudo conectar con el backend (${API_BASE}). Verifica que la API esté levantada.`
  : 'No se pudo conectar con el backend. Verifica que el servicio esté levantado (docker compose ps) y que Nginx haga proxy de /api.'

const TIMEOUT_MS = 20000

export class ApiError extends Error {
  constructor(mensaje, { status = 0, tipo = 'red', detalle = null } = {}) {
    super(mensaje)
    this.name = 'ApiError'
    this.status = status
    // 'red'     -> no hubo respuesta (backend caído, sin red, timeout)
    // 'http'    -> hubo respuesta pero con status no-ok
    // 'respuesta' -> respuesta ok pero con un cuerpo que no es JSON válido
    this.tipo = tipo
    this.detalle = detalle
  }
}

/** True si el error se debió a que el backend no respondió. */
export function esErrorDeConexion(error) {
  return Boolean(error) && (error.tipo === 'red' || error.name === 'TypeError')
}

/** Normaliza el cuerpo de error de FastAPI a un texto legible. */
function extraerDetalle(datos, status) {
  if (datos && typeof datos === 'object') {
    const detalle = datos.detail ?? datos.mensaje ?? datos.error
    if (typeof detalle === 'string' && detalle.trim()) return detalle.trim()
    if (Array.isArray(detalle)) {
      const mensajes = detalle
        .map((item) => (typeof item === 'string' ? item : item && item.msg))
        .filter(Boolean)
      if (mensajes.length > 0) return mensajes.join('. ')
    }
    // Algunos endpoints (p. ej. la ingesta de CSV) devuelven el detalle como
    // objeto {mensaje, archivo, salida}; se muestra el mensaje y un extracto
    // de la salida, no el JSON completo.
    if (detalle && typeof detalle === 'object') {
      const mensaje = detalle.mensaje ?? detalle.message ?? detalle.error
      if (typeof mensaje === 'string' && mensaje.trim()) {
        const salida = typeof detalle.salida === 'string' ? detalle.salida.trim() : ''
        return salida
          ? `${mensaje.trim()} ${salida.slice(0, 400)}`
          : mensaje.trim()
      }
    }
    if (detalle) return JSON.stringify(detalle)
  }
  if (typeof datos === 'string' && datos.trim()) return datos.trim()
  return `El servidor respondió con un error (HTTP ${status}).`
}

/**
 * Ejecuta un fetch contra la API y devuelve el cuerpo JSON ya parseado.
 * Lanza siempre ApiError ante cualquier problema.
 */
export async function apiFetch(ruta, opciones = {}) {
  const { timeoutMs = TIMEOUT_MS, ...resto } = opciones
  const url = /^https?:\/\//i.test(ruta) ? ruta : `${API_BASE}${ruta}`

  const headers = { Accept: 'application/json', ...(resto.headers || {}) }
  // Solo los cuerpos de texto (JSON) llevan Content-Type manual. Con FormData
  // NO se debe fijar: el navegador añade "multipart/form-data; boundary=...".
  if (
    typeof resto.body === 'string' &&
    !headers['Content-Type'] &&
    !headers['content-type']
  ) {
    headers['Content-Type'] = 'application/json'
  }

  // Timeout propio para no dejar la UI colgada si el backend no responde.
  const controlador = new AbortController()
  const temporizador = setTimeout(() => controlador.abort(), timeoutMs)

  let respuesta
  try {
    respuesta = await fetch(url, { ...resto, headers, signal: controlador.signal })
  } catch (error) {
    if (error && error.name === 'AbortError') {
      throw new ApiError(
        'El backend tardó demasiado en responder. Verifica que esté levantado y accesible.',
        { tipo: 'red', detalle: error },
      )
    }
    throw new ApiError(MENSAJE_CONEXION, { tipo: 'red', detalle: error })
  } finally {
    clearTimeout(temporizador)
  }

  // Leemos como texto para no romper si el servidor devuelve HTML (p. ej. un
  // proxy caído) o un cuerpo vacío (204).
  const texto = await respuesta.text().catch(() => '')
  let datos = null
  if (texto) {
    try {
      datos = JSON.parse(texto)
    } catch {
      datos = texto
    }
  }

  if (!respuesta.ok) {
    throw new ApiError(extraerDetalle(datos, respuesta.status), {
      status: respuesta.status,
      tipo: 'http',
      detalle: datos,
    })
  }

  if (respuesta.status === 204 || !texto) return null

  if (typeof datos === 'string') {
    throw new ApiError(
      'El servidor devolvió una respuesta no válida (se esperaba JSON).',
      { status: respuesta.status, tipo: 'respuesta', detalle: texto.slice(0, 300) },
    )
  }

  return datos
}

export function apiGet(ruta, opciones = {}) {
  return apiFetch(ruta, { ...opciones, method: 'GET' })
}

/**
 * GET que devuelve `null` cuando el recurso no existe (HTTP 404) en lugar de
 * lanzar. Cualquier otro error se propaga igual que en apiGet.
 */
export async function apiGetOpcional(ruta, opciones = {}) {
  try {
    return await apiGet(ruta, opciones)
  } catch (error) {
    if (error && error.status === 404) return null
    throw error
  }
}

export function apiPost(ruta, cuerpo, opciones = {}) {
  return apiFetch(ruta, {
    ...opciones,
    method: 'POST',
    body: JSON.stringify(cuerpo ?? {}),
  })
}

export function apiPut(ruta, cuerpo, opciones = {}) {
  return apiFetch(ruta, {
    ...opciones,
    method: 'PUT',
    body: JSON.stringify(cuerpo ?? {}),
  })
}

/**
 * Sube archivos como multipart/form-data (campo `archivo`).
 *
 * Importante: NO se fija Content-Type a propósito. El navegador lo genera con
 * el boundary correcto; si se fijara "multipart/form-data" a mano, el backend
 * no podría separar las partes del formulario.
 */
export async function apiUpload(ruta, formData, opciones = {}) {
  if (typeof FormData === 'undefined' || !(formData instanceof FormData)) {
    throw new ApiError('No se pudo preparar el archivo para enviarlo al servidor.', {
      tipo: 'local',
    })
  }

  return apiFetch(ruta, {
    // Los CSV pueden tardar más que una petición normal en procesarse.
    timeoutMs: 60000,
    ...opciones,
    method: 'POST',
    body: formData,
  })
}

/** Devuelve solo los objetos de una respuesta en forma de arreglo. */
export function listaDeObjetos(datos) {
  if (!Array.isArray(datos)) return []
  return datos.filter((item) => item && typeof item === 'object' && !Array.isArray(item))
}

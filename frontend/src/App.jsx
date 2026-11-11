import { useCallback, useRef, useState } from 'react'
import Login from './components/Login.jsx'
import OfertaTable from './components/OfertaTable.jsx'
import Horario from './components/Horario.jsx'
import GenerarHorario from './components/GenerarHorario.jsx'
import AdminReglas from './components/AdminReglas.jsx'
import MatrizEstudiante from './components/MatrizEstudiante.jsx'
import SubirOferta from './components/SubirOferta.jsx'
import OfertaAdmin from './components/OfertaAdmin.jsx'
import Banner from './components/Banner.jsx'
import Spinner from './components/Spinner.jsx'
import {
  API_BASE,
  apiGet,
  apiGetOpcional,
  apiPost,
  esErrorDeConexion,
  listaDeObjetos,
} from './api.js'

const CLASE_TAB_ACTIVA = 'bg-white text-indigo-800 shadow'
const CLASE_TAB_INACTIVA = 'text-indigo-100 hover:bg-indigo-600 hover:text-white'

export default function App() {
  const [vista, setVista] = useState('estudiante') // 'estudiante' | 'admin'
  const [estudianteId, setEstudianteId] = useState(null)
  const [oferta, setOferta] = useState([])
  const [horario, setHorario] = useState([])
  const [cargando, setCargando] = useState(false)
  const [inscribiendoNrc, setInscribiendoNrc] = useState(null)
  const [mensaje, setMensaje] = useState(null)
  // Se incrementa al terminar una subida de oferta: OfertaAdmin lo escucha
  // para volver a consultar GET /api/oferta sin recargar la página.
  const [refreshOfertaAdmin, setRefreshOfertaAdmin] = useState(0)

  // Matriz de estudio del estudiante (para habilitar el horario automático).
  const [matriz, setMatriz] = useState(null)
  const [cargandoMatriz, setCargandoMatriz] = useState(false)
  const [errorMatriz, setErrorMatriz] = useState(null)
  // Descarta respuestas viejas si el estudiante cambia o sube otra matriz.
  const solicitudMatrizRef = useRef(0)

  // Carga (o recarga) la oferta y el horario del estudiante. Usa allSettled para
  // que un endpoint caído no impida mostrar el otro.
  const cargarDatos = useCallback(async (id, { limpiarMensaje = false } = {}) => {
    if (id == null) return

    if (limpiarMensaje) setMensaje(null)
    setCargando(true)

    const [resultadoOferta, resultadoHorario] = await Promise.allSettled([
      apiGet('/api/oferta'),
      apiGet(`/api/horario/${id}`),
    ])

    let error = null

    if (resultadoOferta.status === 'fulfilled') {
      setOferta(listaDeObjetos(resultadoOferta.value))
    } else {
      error = resultadoOferta.reason
    }

    if (resultadoHorario.status === 'fulfilled') {
      setHorario(listaDeObjetos(resultadoHorario.value))
    } else {
      error = error || resultadoHorario.reason
    }

    if (error) {
      setMensaje({
        tipo: 'error',
        texto: error.message || 'Ocurrió un error al cargar los datos.',
        conexion: esErrorDeConexion(error),
        reintentable: true,
      })
    }

    setCargando(false)
  }, [])

  // Intenta recuperar la matriz ya registrada. Un 404 significa que el
  // estudiante todavía no la ha cargado: no es un error, solo se muestra el
  // formulario de carga.
  const cargarMatriz = useCallback(async (id) => {
    if (id == null) return null

    const solicitud = ++solicitudMatrizRef.current
    setCargandoMatriz(true)
    setErrorMatriz(null)

    try {
      const datos = await apiGetOpcional(`/api/estudiante/${id}/matriz`)
      if (solicitud !== solicitudMatrizRef.current) return null
      setMatriz(datos ?? null)
      return datos ?? null
    } catch (error) {
      if (solicitud !== solicitudMatrizRef.current) return null
      // No se borra una matriz ya cargada: el fallo es de la consulta, no de
      // los datos que el estudiante ya tenía en pantalla.
      setErrorMatriz(
        error.message || 'No se pudo consultar tu matriz de estudio.',
      )
      return null
    } finally {
      if (solicitud === solicitudMatrizRef.current) setCargandoMatriz(false)
    }
  }, [])

  function entrar(id) {
    setEstudianteId(id)
    setMensaje(null)
    setMatriz(null)
    setErrorMatriz(null)
    cargarDatos(id)
    cargarMatriz(id)
  }

  // La subida de la matriz ya trae su resumen: invalida cualquier GET en vuelo
  // y desbloquea el botón de generar horario.
  function actualizarMatriz(datos) {
    solicitudMatrizRef.current += 1
    setCargandoMatriz(false)
    setErrorMatriz(null)
    setMatriz(datos ?? null)
  }

  async function inscribir(nrcId) {
    if (estudianteId == null || inscribiendoNrc != null) return

    setMensaje(null)
    setInscribiendoNrc(nrcId)

    try {
      const datos = await apiPost('/api/inscribir', {
        estudiante_id: estudianteId,
        nrc_id: nrcId,
      })
      setMensaje({
        tipo: 'ok',
        texto: (datos && datos.mensaje) || 'Inscripción exitosa.',
      })
      // Refresca oferta (cupos) y horario sin borrar el mensaje de éxito.
      await cargarDatos(estudianteId)
    } catch (error) {
      setMensaje({
        tipo: 'error',
        texto: error.message || 'No se pudo completar la inscripción.',
        conexion: esErrorDeConexion(error),
        reintentable: false,
      })
    } finally {
      setInscribiendoNrc(null)
    }
  }

  function salir() {
    solicitudMatrizRef.current += 1
    setEstudianteId(null)
    setOferta([])
    setHorario([])
    setMensaje(null)
    setInscribiendoNrc(null)
    setMatriz(null)
    setCargandoMatriz(false)
    setErrorMatriz(null)
  }

  function reintentar() {
    if (estudianteId != null) {
      cargarDatos(estudianteId, { limpiarMensaje: true })
    }
  }

  // La matriz solo se considera cargada si el GET/POST devolvió datos; un 404
  // deja el estado en null y muestra el formulario de carga.
  const matrizCargada = matriz != null

  return (
    <div className="min-h-screen bg-gray-100">
      <header className="bg-indigo-700 text-white shadow">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-4 py-4">
          <div>
            <h1 className="text-xl font-bold leading-tight">Inscripción de Materias</h1>
            <p className="text-xs text-indigo-200">
              Portal del estudiante y panel administrativo
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {vista === 'estudiante' && estudianteId != null && (
              <span className="rounded-full bg-indigo-800/60 px-3 py-1 text-xs">
                Estudiante ID: {estudianteId}
              </span>
            )}

            <nav
              role="tablist"
              aria-label="Seleccionar vista"
              className="flex rounded-lg bg-indigo-800/60 p-1"
            >
              <button
                type="button"
                role="tab"
                aria-selected={vista === 'estudiante'}
                onClick={() => setVista('estudiante')}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  vista === 'estudiante' ? CLASE_TAB_ACTIVA : CLASE_TAB_INACTIVA
                }`}
              >
                Vista Estudiante
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={vista === 'admin'}
                onClick={() => setVista('admin')}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  vista === 'admin' ? CLASE_TAB_ACTIVA : CLASE_TAB_INACTIVA
                }`}
              >
                Vista Administrador
              </button>
            </nav>

            {vista === 'estudiante' && estudianteId != null && (
              <button
                type="button"
                onClick={salir}
                className="rounded-md bg-indigo-500 px-3 py-1.5 text-sm font-medium hover:bg-indigo-400"
              >
                Salir
              </button>
            )}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-6">
        {vista === 'estudiante' && mensaje && (
          <div className="mb-4">
            <Banner
              tipo={mensaje.tipo}
              onCerrar={() => setMensaje(null)}
              accion={
                mensaje.reintentable && estudianteId != null ? (
                  <button
                    type="button"
                    onClick={reintentar}
                    className="rounded-md border border-red-300 bg-white px-3 py-1 text-xs font-semibold text-red-700 hover:bg-red-50"
                  >
                    Reintentar
                  </button>
                ) : null
              }
            >
              {mensaje.texto}
            </Banner>
          </div>
        )}

        {vista === 'admin' ? (
          <div className="space-y-6">
            <div>
              <h2 className="text-xl font-semibold text-gray-800">
                Panel de administración
              </h2>
              <p className="text-sm text-gray-500">
                Carga la oferta académica y administra las reglas de
                prerrequisitos.
              </p>
            </div>

            <SubirOferta
              onSubida={() => {
                // La tabla de la oferta se refresca siempre; si además hay un
                // estudiante logueado, se actualizan sus cupos y su horario.
                setRefreshOfertaAdmin((actual) => actual + 1)
                if (estudianteId != null) cargarDatos(estudianteId)
              }}
            />

            <OfertaAdmin refreshKey={refreshOfertaAdmin} />

            <AdminReglas />
          </div>
        ) : estudianteId == null ? (
          <Login onEntrar={entrar} />
        ) : (
          <div className="space-y-6">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-xl font-semibold text-gray-800">Mi inscripción</h2>
              {cargando && (
                <Spinner label="Actualizando datos…" className="h-4 w-4" />
              )}
            </div>

            <MatrizEstudiante
              estudianteId={estudianteId}
              matriz={matriz}
              cargando={cargandoMatriz}
              errorCarga={errorMatriz}
              onMatrizCargada={actualizarMatriz}
              onRecargar={() => cargarMatriz(estudianteId)}
            />
            <GenerarHorario
              estudianteId={estudianteId}
              matrizCargada={matrizCargada}
              matrizCargando={cargandoMatriz}
              onGenerado={() => cargarDatos(estudianteId, { limpiarMensaje: true })}
            />
            <Horario horario={horario} cargando={cargando} />
            <OfertaTable
              oferta={oferta}
              onInscribir={inscribir}
              inscribiendoNrc={inscribiendoNrc}
              cargando={cargando}
            />
          </div>
        )}
      </main>

      <footer className="mx-auto max-w-6xl px-4 pb-8 text-center text-xs text-gray-400">
        API: <span className="font-mono">{API_BASE || 'mismo origen (proxy /api)'}</span>
      </footer>
    </div>
  )
}

const ESTILOS = {
  error: 'bg-red-50 text-red-800 border-red-200',
  ok: 'bg-green-50 text-green-800 border-green-200',
  aviso: 'bg-amber-50 text-amber-800 border-amber-200',
  info: 'bg-sky-50 text-sky-800 border-sky-200',
}

const ICONOS = {
  error: '⚠️',
  ok: '✅',
  aviso: '⚠️',
  info: 'ℹ️',
}

/**
 * Mensaje reutilizable para errores de red, respuestas del backend y avisos.
 * `tipo` admite: 'error' | 'ok' | 'aviso' | 'info'.
 */
export default function Banner({ tipo = 'info', titulo, children, accion, onCerrar }) {
  if (!children && !titulo) return null

  const estilo = ESTILOS[tipo] || ESTILOS.info

  return (
    <div
      role={tipo === 'error' ? 'alert' : 'status'}
      className={`flex items-start gap-3 rounded-lg border px-4 py-3 text-sm shadow-sm ${estilo}`}
    >
      <span aria-hidden="true" className="text-base leading-5">
        {ICONOS[tipo] || ICONOS.info}
      </span>

      <div className="min-w-0 flex-1">
        {titulo && <p className="font-semibold">{titulo}</p>}
        {children && <div className={titulo ? 'mt-1' : ''}>{children}</div>}
      </div>

      {accion && <div className="shrink-0">{accion}</div>}

      {onCerrar && (
        <button
          type="button"
          onClick={onCerrar}
          aria-label="Cerrar mensaje"
          className="shrink-0 rounded px-1 text-lg leading-none opacity-60 transition-opacity hover:opacity-100"
        >
          ×
        </button>
      )}
    </div>
  )
}

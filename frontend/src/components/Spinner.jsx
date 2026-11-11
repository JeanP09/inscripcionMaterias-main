/**
 * Indicador de carga reutilizable (sin dependencias externas).
 *
 * - `label`: texto que acompaña al spinner. Pasa `null` o `''` para mostrar
 *   solo el círculo (útil dentro de botones).
 * - `className`: tamaño del círculo, p. ej. "h-4 w-4" o "h-8 w-8".
 * - `centrado`: bloquea el ancho y centra el contenido (estados de sección).
 *
 * El color se hereda con `border-current`, así el spinner se adapta al texto
 * del botón o de la sección donde se use.
 */
export default function Spinner({
  label = 'Cargando…',
  className = 'h-5 w-5',
  centrado = false,
}) {
  return (
    <span
      role="status"
      aria-live="polite"
      className={
        centrado
          ? 'flex w-full flex-col items-center justify-center gap-2 py-6 text-center text-gray-500'
          : 'inline-flex items-center gap-2'
      }
    >
      <span
        aria-hidden="true"
        className={`inline-block shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent ${className}`}
      />
      {label ? <span className="text-sm">{label}</span> : null}
    </span>
  )
}

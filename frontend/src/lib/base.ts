/**
 * Der Unterpfad, unter dem nexcrate laeuft, etwa `/nexcrate` hinter einem Proxy (wie Arrs "URL Base"). Der Server
 * schreibt ihn beim Ausliefern der Seite in `<meta name="nexcrate-base">`; ohne ihn ist er leer und alles bleibt
 * unter `/`. Jede Adresse des eigenen Servers, die mit `/` beginnt, geht durch `withBase`.
 */

function readBase(): string {
  if (typeof document === 'undefined') return ''
  const value = document.querySelector('meta[name="nexcrate-base"]')?.getAttribute('content') ?? ''
  // Nur ein Pfad aus Buchstaben, Ziffern, Bindestrich, Unterstrich und Punkt je Teil; alles andere gilt nicht.
  if (!/^(\/[A-Za-z0-9._-]+)+$/.test(value)) return ''
  return value
}

export const URL_BASE = readBase()

/** Eine Adresse des eigenen Servers mit dem Unterpfad davor; eine fremde oder relative bleibt, wie sie ist. */
export function withBase(path: string): string {
  if (!path.startsWith('/') || path.startsWith('//')) return path
  return URL_BASE + path
}

import { withBase } from '../lib/base'

/** Bilder der Bibliothek und Poster aus der TMDB-Suche. Beide liefert der eigene Server, als Pfad ohne Unterpfad. */
const OWN_IMAGE_PATHS = ['/api/images/', '/api/tmdb/poster/']

/**
 * Poster kommen nur vom eigenen Server, nie direkt von TMDB oder Radarr: nexcrate holt sie
 * und speichert sie zwischen. Eine andere Adresse im Feld wird nicht geladen, dann steht der
 * Platzhalter da.
 *
 * `?v=` in der Adresse aendert sich mit dem Bild: Unter derselben Adresse laedt der
 * Browser kein neues.
 */
export function posterSrc(url: string | null | undefined): string | null {
  if (typeof url !== 'string') return null
  return OWN_IMAGE_PATHS.some((prefix) => url.startsWith(prefix)) ? withBase(url) : null
}

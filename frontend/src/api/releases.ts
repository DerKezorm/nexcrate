import { api } from './client'
import type { AlbumCheck, ReleaseCheck, ReleaseCheckRequest, SeriesCheck, SeriesCheckRequest } from './types'

/** So lang darf ein Release-Name sein. */
export const RELEASE_NAME_MAX = 500

/** So viele Namen nimmt der Serien-Pruefer auf einmal. */
export const SERIES_NAMES_MAX = 50

/** Und so viele der Pruefer fuer Musik. */
export const ALBUM_NAMES_MAX = 50

export const releasesApi = {
  /** Bewertet einen Namen gegen jede Fassung fuer Filme. Nichts wird gesucht oder geladen. */
  check: (body: ReleaseCheckRequest) => api.post<ReleaseCheck>('/releases/check', body),
  /** Dasselbe fuer Serien, mit bis zu 50 Namen und wahlweise einer Serie aus der Bibliothek. */
  checkSeries: (body: SeriesCheckRequest) => api.post<SeriesCheck>('/releases/check-series', body),
  /** Musik M2: bis zu 50 Namen gegen das eine Musik-Profil. Ohne Profil kommt nur, was nexcrate liest. */
  checkAlbum: (names: string[]) => api.post<AlbumCheck>('/releases/check-album', { names }),
}

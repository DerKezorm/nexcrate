import { api, ApiError } from './client'
import type { AlbumSearch, AlbumSearchStartBody, Search, SearchStart, SearchStartBody } from './types'

/** So oft fragt die Titelseite nach, solange eine Suche laeuft. */
export const SEARCH_POLL_MS = 1000

/**
 * Die Suche je Film, wie in the design notes unter "API". Gesucht wird beim Indexer,
 * geladen wird nichts. Antworten tragen weder Download- noch Info-Adressen.
 */
export const searchesApi = {
  /**
   * 202 `{search_id}`. 409 `search_running {search_id}`, wenn fuer den Titel schon eine laeuft;
   * 409 `search_busy`, `no_indexers`; 422 `invalid_input` fuer einen Umfang, der nicht zum Titel passt.
   * Seit S3 mit `body` fuer eine Serie: ganze Serie, Staffel oder Folge; 409 `anime_later`. Ein Film schickt keinen.
   */
  start: (titleId: string | number, body?: SearchStartBody) =>
    api.post<SearchStart>(`/library/${encodeURIComponent(String(titleId))}/search`, body),
  /** 404 `not_found`, sobald die Suche abgelaufen ist. */
  get: (searchId: string, signal?: AbortSignal) => api.get<Search>(`/searches/${encodeURIComponent(searchId)}`, { signal }),
}

/**
 * Seit M3 die Suche nach einem Album. 202 `{search_id}`; 409 `search_running`, `search_busy`, `no_indexers`,
 * `album_no_artist`. Mit `aliases` fragt sie zusaetzlich unter bis zu zwei weiteren Namen des Kuenstlers.
 */
export const albumSearchesApi = {
  start: (titleId: string | number, body?: AlbumSearchStartBody) =>
    api.post<SearchStart>(`/music/albums/${encodeURIComponent(String(titleId))}/search`, body),
  get: (searchId: string, signal?: AbortSignal) =>
    api.get<AlbumSearch>(`/music/searches/${encodeURIComponent(searchId)}`, { signal }),
}

/** Die Nummer der Suche, die fuer diesen Titel schon laeuft (409 `search_running`). Sonst null. */
export function runningSearchId(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.status !== 409 || error.code !== 'search_running') return null
  const id = error.values.search_id
  if (typeof id === 'string' && id !== '') return id
  if (typeof id === 'number' && Number.isFinite(id)) return String(id)
  return null
}

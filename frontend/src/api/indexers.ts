import { api } from './client'
import type { Indexer, IndexerCreate, IndexerFromSource, IndexerKind, IndexerSearch, IndexerTest, IndexerTestResult, IndexerUpdate } from './types'

export const INDEXER_KINDS: readonly IndexerKind[] = ['newznab', 'torznab']

/** Die ueblichen Filmkategorien, wenn ein Indexer keine nennt. Wie im Plan unter "Caps". */
export const DEFAULT_MOVIE_CATEGORIES: readonly number[] = [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060]

/**
 * Das Tageslimit, das man selbst eintraegt (Schritt 3c). Leer heisst keins. Eine Zahl ausserhalb geht gar nicht erst
 * hinaus; der Server prueft trotzdem (422 `invalid_input`).
 */
export const DAILY_LIMIT_MIN = 1
export const DAILY_LIMIT_MAX = 100_000

/** Filme liegen bei Newznab und Torznab in 2000 bis 2999. */
export function isMovieCategory(id: number): boolean {
  return id >= 2000 && id <= 2999
}

/**
 * Newznab und Torznab. Der Schluessel geht nur hinaus, nie herein: Antworten tragen
 * `has_api_key`, und Suchantworten enthalten keine Download-Links.
 */
/** Radarrs "Indexer-Merkmale bevorzugen": gilt fuer alle Indexer, nur fuer Filme, ab Werk aus. */
export const indexerFlagsSwitch = {
  get: async () => ({ enabled: (await api.get<{ prefer_indexer_flags: boolean }>('/indexers/options')).prefer_indexer_flags === true }),
  save: async (enabled: boolean) => ({
    enabled: (await api.put<{ prefer_indexer_flags: boolean }>('/indexers/options', { prefer_indexer_flags: enabled })).prefer_indexer_flags === true,
  }),
}

export const indexersApi = {
  list: () => api.get<Indexer[]>('/indexers'),
  /** `{caps}` oder ein Indexer-Fehler. */
  test: (body: IndexerTest) => api.post<IndexerTestResult>('/indexers/test', body),
  /** Prueft vor dem Speichern. 422 `indexer_url_invalid`, `indexer_no_categories`. */
  create: (body: IndexerCreate) => api.post<Indexer>('/indexers', body),
  /** Eine neue Adresse oder ein neuer Schluessel wird geprueft. */
  update: (id: number, body: IndexerUpdate) => api.patch<Indexer>(`/indexers/${id}`, body),
  remove: (id: number) => api.delete<void>(`/indexers/${id}`),
  /** Eine Testsuche. Kein Treffer ist ein Erfolg mit leerer Liste, kein Fehler. */
  search: (id: number, q: string) => api.post<IndexerSearch>(`/indexers/${id}/search`, { q }),
  /**
   * Einen Indexer aus Radarr oder Sonarr uebernehmen, mit dem Schluessel, den keines von beiden herausgibt. Aus Sonarr
   * (S6, Entscheidung 14) werden seine Kategorien die Serienkategorien; einen Indexer mit derselben Adresse lehnt der
   * Server dann nicht ab (409 `indexer_exists`), sondern ergaenzt dessen Serienkategorien und antwortet 201 mit ihm.
   * Dafuer darf `api_key` leer bleiben.
   */
  fromSource: (body: IndexerFromSource) => api.post<Indexer>('/indexers/from-source', body),
}

/**
 * Seit S3: die Serienkategorien, wenn ein Indexer keine nennt. Sonarrs Vorgabe plus UHD, wie im Plan S3 unter
 * Entscheidung 5. Anime (5070) kommt spaeter und steht nie darin.
 */
export const DEFAULT_SERIES_CATEGORIES: readonly number[] = [5030, 5040, 5045]

export const ANIME_CATEGORY = 5070

/** Serien liegen bei Newznab und Torznab in 5000 bis 5999. */
export function isSeriesCategory(id: number): boolean {
  return id >= 5000 && id <= 5999
}

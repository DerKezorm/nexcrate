import { api } from './client'
import type { DiscoverAlbums, DiscoverGenre, DiscoverState, DiscoverTitles } from './types'

export type DiscoverKind = 'movie' | 'series' | 'album'

/** Die Listen je Art, in der Reihenfolge der Knoepfe. Die erste ist die Vorgabe. */
export const DISCOVER_LISTS = {
  movie: ['fresh', 'popular', 'acclaimed', 'classics'],
  series: ['new', 'popular', 'ended', 'classics'],
  album: ['fresh', 'trending', 'classics'],
} as const

export type DiscoverList<K extends DiscoverKind> = (typeof DISCOVER_LISTS)[K][number]

/** Filter fuer Filme und Serien. Leer heisst: kein Filter. */
export type DiscoverFilters = { region: string; genre: string; language: string; country: string }

export const NO_FILTERS: DiscoverFilters = { region: '', genre: '', language: '', country: '' }

function query(entries: Record<string, string>): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(entries)) if (value !== '') params.set(key, value)
  return params.toString()
}

/** Die Adresse einer Liste. Filme kennen die Region, Serien das Herkunftsland; Klassiker bei Filmen ohne Region. */
export function discoverPath(kind: 'movie' | 'series', list: string, filters: DiscoverFilters): string {
  const base = { list, genre: filters.genre, language: filters.language }
  if (kind === 'series') return `/discover/series?${query({ ...base, country: filters.country })}`
  return `/discover/movies?${query({ ...base, region: list === 'classics' ? '' : filters.region })}`
}

/**
 * Entdecken: je Liste hoechstens 20 Titel, die nicht in der Bibliothek stehen. Filme und Serien kommen von TMDB,
 * Alben von ListenBrainz hinter einem eigenen Schalter.
 */
export const discoverApi = {
  state: () => api.get<DiscoverState>('/discover'),
  switchListenBrainz: (enabled: boolean) => api.put<DiscoverState>('/discover/listenbrainz', { enabled }),
  titles: (kind: 'movie' | 'series', list: string, filters: DiscoverFilters, signal?: AbortSignal) =>
    api.get<DiscoverTitles>(discoverPath(kind, list, filters), { signal }),
  albums: (list: string, signal?: AbortSignal) => api.get<DiscoverAlbums>(`/discover/albums?${query({ list })}`, { signal }),
  genres: (kind: 'movie' | 'series') => api.get<DiscoverGenre[]>(`/discover/genres?kind=${kind}`),
  hide: (kind: DiscoverKind, key: string) => api.post<void>('/discover/hidden', { kind, key }),
  resetHidden: () => api.delete<{ removed: number }>('/discover/hidden'),
}

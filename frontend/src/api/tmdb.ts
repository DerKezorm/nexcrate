import { api } from './client'
import type { TmdbSearch, TmdbState } from './types'

export type TmdbQuery = {
  q: string
  /** Wie getippt. Mit geht es nur mit vier Ziffern. */
  year: string
  page?: number
  /** Series since S1; without it the server searches movies. */
  kind?: 'movie' | 'series'
}

/** Frueher und spaeter nimmt der Server nicht an (422). */
const FIRST_YEAR = 1870
const LAST_YEAR = 2100

/** Ein Jahr mit vier Ziffern, das der Server annimmt, sonst leer. Eine halbe Eingabe wie "19" geht nicht mit. */
export function cleanYear(year: string): string {
  const value = year.trim()
  if (!/^\d{4}$/.test(value)) return ''
  const number = Number(value)
  return number >= FIRST_YEAR && number <= LAST_YEAR ? value : ''
}

export function tmdbSearchPath({ q, year, page = 1, kind = 'movie' }: TmdbQuery): string {
  const params = new URLSearchParams({ q: q.trim() })
  const cleaned = cleanYear(year)
  if (cleaned !== '') params.set('year', cleaned)
  params.set('page', String(page))
  if (kind === 'series') params.set('kind', 'series')
  return `/tmdb/search?${params.toString()}`
}

/**
 * Filme finden ueber TMDB. Der Browser spricht nur mit nexcrate, nexcrate mit TMDB.
 * Der Token geht nur hinaus, nie herein: Antworten tragen `configured`.
 */
export const tmdbApi = {
  state: () => api.get<TmdbState>('/tmdb'),
  /** Prueft den Token bei TMDB und speichert ihn nur, wenn er geht. Sonst ein TMDB-Fehler. */
  save: (token: string) => api.put<TmdbState>('/tmdb', { token }),
  /** Danach sucht nexcrate nichts mehr bei TMDB. */
  remove: () => api.delete<void>('/tmdb'),
  /** 409 `tmdb_not_configured`, solange kein Token gespeichert ist. */
  search: (query: TmdbQuery, signal?: AbortSignal) => api.get<TmdbSearch>(tmdbSearchPath(query), { signal }),
}

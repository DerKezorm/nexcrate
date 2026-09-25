import { api } from './client'

/** Die Art eines Eintrags, nicht die eines Titels: eine Folge gehoert zu einer Serie. */
export type CalendarKind = 'movie' | 'episode' | 'album'

/** Warum der Titel an diesem Tag steht. */
export type CalendarOccasion = 'theatrical' | 'digital' | 'physical' | 'air' | 'release'

export const CALENDAR_KINDS: readonly CalendarKind[] = ['movie', 'episode', 'album']

export type CalendarEntry = {
  day: string
  kind: CalendarKind
  occasion: CalendarOccasion
  /** Das Land, aus dem der Termin kommt, wenn es nicht die eingestellte Region ist. */
  country: string | null
  title_id: number
  title: string
  year: number | null
  season?: number | null
  episode?: number | null
  episode_title?: string | null
  artist?: string | null
  monitored: boolean
  has_file: boolean
}

export type CalendarPage = {
  from: string
  to: string
  region: string
  items: CalendarEntry[]
  counts: Record<string, number>
  truncated: boolean
}

export type CalendarSettings = {
  region: string
  feed_enabled: boolean
  feed_path: string
  feed_key: string
}

export type RegionCount = { country: string; movies: number }

export type CalendarQuery = {
  from: string
  to: string
  kinds?: readonly CalendarKind[]
  monitored?: boolean
  region?: string
}

/**
 * Der Release-Kalender: ein Zeitraum auf einmal, nie ein Tag nach dem anderen.
 * Die Termine der Filme kommen aus der eingestellten Region.
 */
export const calendarApi = {
  span(query: CalendarQuery, signal?: AbortSignal) {
    const params = new URLSearchParams({ from: query.from, to: query.to })
    if (query.kinds && query.kinds.length > 0 && query.kinds.length < CALENDAR_KINDS.length) {
      params.set('kinds', query.kinds.join(','))
    }
    if (query.monitored === true) params.set('monitored', 'true')
    if (query.region !== undefined) params.set('region', query.region)
    return api.get<CalendarPage>(`/calendar?${params.toString()}`, { signal })
  },
  regions(signal?: AbortSignal) {
    return api.get<{ items: RegionCount[] }>('/calendar/regions', { signal })
  },
  settings(signal?: AbortSignal) {
    return api.get<CalendarSettings>('/calendar/settings', { signal })
  },
  saveSettings(body: { region?: string; feed_enabled?: boolean }) {
    return api.put<CalendarSettings>('/calendar/settings', body)
  },
  newKey() {
    return api.post<CalendarSettings>('/calendar/settings/key')
  },
}

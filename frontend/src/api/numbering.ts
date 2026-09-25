import { api } from './client'
import type { SeriesNumbering, SeriesNumberingUpdate, XemSettings } from './types'

/** So viele Suchtitel nimmt der Server je Serie, jeder 1 bis 200 Zeichen. */
export const ALIASES_MAX = 50
export const ALIAS_LENGTH_MAX = 200

/**
 * Wie Releases eine Serie zaehlen (S3.2): Episodengruppe, Suchtitel, Korrekturen je Folge.
 * Dazu der Schalter fuer TheXEM, einen Weg nach draussen.
 */
export const numberingApi = {
  get: (titleId: number, signal?: AbortSignal) => api.get<SeriesNumbering>(`/series/${titleId}/numbering`, { signal }),
  /** 422 `invalid_input`, 502 `episode_group_unavailable`, 409 `tmdb_not_configured`. Listen ersetzen alles. */
  save: (titleId: number, body: SeriesNumberingUpdate) => api.put<SeriesNumbering>(`/series/${titleId}/numbering`, body),
}

export const xemApi = {
  get: () => api.get<XemSettings>('/settings/xem'),
  save: (enabled: boolean) => api.put<XemSettings>('/settings/xem', { enabled }),
}

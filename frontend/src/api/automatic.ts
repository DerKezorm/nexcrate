import { api } from './client'
import type { AutomaticSearchStart, AutomaticState, UpgradesState } from './types'

/** So oft fragt die Titelseite nach, solange eine gerade gestartete automatische Suche noch kein Ergebnis hat. */
export const AUTOMATIC_POLL_MS = 3000

/** Nach so vielen Nachfragen hoert sie auf. Eine Suche ist nach spaetestens drei Minuten fertig. */
export const AUTOMATIC_POLL_MAX = 60

/**
 * Die Schalter fuer alles Automatische, wie in the design notes unter "API" und the design notes
 * (Entscheidung 1): `enabled` fuer Filme, `series_enabled` fuer Serien. Neue Releases liest nexcrate, sobald einer an
 * ist. Beide ab Werk aus.
 */
export const automaticApi = {
  get: () => api.get<AutomaticState>('/automatic'),
  save: (enabled: boolean) => api.put<AutomaticState>('/automatic', { enabled }),
  /**
   * "Jetzt automatisch suchen": 202 `{started}`. 409 `automatic_off`, `nothing_wanted`, `search_running`. Die Suche
   * laeuft am Server weiter; was sie gefunden hat, steht danach im `search_plan` des Titels.
   */
  searchNow: (titleId: number) => api.post<AutomaticSearchStart>(`/library/${titleId}/search/automatic`),
}

/**
 * Verbesserungen bremsen (24.09.2026): `paused` pausiert die Verbesserungen von allem, was jetzt da ist, `per_day`
 * begrenzt automatische Verbesserungen in 24 Stunden (0: keine Grenze). Was fehlt, halten beide nie auf.
 */
export const upgradesApi = {
  get: (signal?: AbortSignal) => api.get<UpgradesState>('/automatic/upgrades', { signal }),
  save: (body: { paused?: boolean; per_day?: number }) => api.put<UpgradesState>('/automatic/upgrades', body),
}

/** Seit Musik M5 der dritte Schalter, fuer Alben (Entscheidung 1), ab Werk aus. */
export const automaticMusicSwitch = {
  get: async () => ({ enabled: (await api.get<AutomaticState>('/automatic')).music_enabled === true }),
  save: async (enabled: boolean) => ({
    enabled: (await api.put<AutomaticState>('/automatic', { music_enabled: enabled })).music_enabled === true,
  }),
}

/** Der Schalter fuer Serien in der Form, die `useSwitchSetting` liest. Stabil, weil hier einmal angelegt. */
export const automaticSeriesSwitch = {
  get: async () => ({ enabled: (await api.get<AutomaticState>('/automatic')).series_enabled === true }),
  save: async (enabled: boolean) => ({
    enabled: (await api.put<AutomaticState>('/automatic', { series_enabled: enabled })).series_enabled === true,
  }),
}

import { api } from './client'

export type WhatsNewState = {
  /** Die laufende Version des Servers. */
  version: string
  /** Die Version, deren "Was ist neu" zuletzt geschlossen wurde; null vor dem ersten Mal. */
  seen: string | null
}

export const whatsNewApi = {
  read: () => api.get<WhatsNewState>('/whats-new'),
  /** Geht nie rueckwaerts: eine aeltere Version als die gespeicherte aendert nichts. */
  seen: (version: string) => api.put<WhatsNewState>('/whats-new', { seen: version }),
}

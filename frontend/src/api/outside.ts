import { api } from './client'

/**
 * Was nach aussen zeigt (V4): Adresse nach aussen und Versionspruefung, Wertungen, Programme,
 * die sich verbinden wollen, Webhooks und die festen Spruenge `/open/…`.
 */

export type UpdateInfo = {
  enabled: boolean
  current: string
  latest: string | null
  /** null, solange die neueste Fassung unbekannt ist. */
  available: boolean | null
  checked_at: string | null
  /** `not_found` (keine Veroeffentlichung oder privates Repo) oder `unreachable`. */
  problem: string | null
  url: string
}

export type SystemSettings = {
  web_url: string | null
  url_base: string
  update: UpdateInfo
}

export type RatingsSettings = {
  imdb_enabled: boolean
  imdb: { loaded_at: string | null; checked_at: string | null; rows: number | null; problem: string | null }
  omdb: { has_key: boolean; today: number; limit: number }
  attribution: string
  omdb_credit: string
}

export type TitleRatings = {
  imdb: { rating: number; votes: number } | null
  rotten_tomatoes: number | null
  metacritic: number | null
  sources: Record<string, string>
}

export type WaitingPairing = {
  pairing_id: string
  app: string
  code: string
  scopes: string[]
  created_at: string
  expires_at: string
}

export type WebhookDelivery = {
  id: number
  event_seq: number | null
  event_type: string
  state: 'pending' | 'delivered' | 'failed' | string
  attempts: number
  next_at: string | null
  status_code: number | null
  error_code: string | null
  created_at: string
  done_at: string | null
}

export type Webhook = {
  id: number
  name: string
  url: string
  types: string[]
  enabled: boolean
  created_at: string
  last: WebhookDelivery | null
  failed: number
}

export const systemSettingsApi = {
  read: () => api.get<SystemSettings>('/system-settings'),
  /** 422 `invalid_input` fuer eine Adresse, die keine ist. */
  change: (body: { web_url?: string; update_check?: boolean }) => api.put<SystemSettings>('/system-settings', body),
  checkNow: () => api.post<UpdateInfo>('/system-settings/update-check'),
}

export const ratingsApi = {
  settings: () => api.get<RatingsSettings>('/ratings/settings'),
  switchImdb: (enabled: boolean) => api.put<RatingsSettings>('/ratings/settings', { imdb_enabled: enabled }),
  refresh: () => api.post<RatingsSettings>('/ratings/imdb/refresh'),
  /** 422 `omdb_key_invalid`, 429 `omdb_limit`, 502 `omdb_unreachable`. */
  storeKey: (key: string) => api.put<RatingsSettings>('/ratings/omdb-key', { key }),
  removeKey: () => api.delete<RatingsSettings>('/ratings/omdb-key'),
  ofTitle: (titleId: number) => api.get<TitleRatings>(`/ratings/title/${titleId}`),
}

export const pairingsApi = {
  waiting: () => api.get<{ items: WaitingPairing[] }>('/pairings'),
  accept: (id: string, scopes: string[]) =>
    api.post<{ key_id: number; name: string; scopes: string[] }>(`/pairings/${encodeURIComponent(id)}/confirm`, { scopes }),
  refuse: (id: string) => api.post<void>(`/pairings/${encodeURIComponent(id)}/deny`),
}

export const webhooksApi = {
  list: () => api.get<{ items: Webhook[]; types: string[] }>('/webhooks'),
  /** 201; das Geheimnis gibt es nur in dieser Antwort. */
  create: (body: { name: string; url: string; types: string[] }) => api.post<{ webhook: Webhook; secret: string }>('/webhooks', body),
  change: (id: number, body: { name?: string; url?: string; types?: string[]; enabled?: boolean }) => api.patch<Webhook>(`/webhooks/${id}`, body),
  remove: (id: number) => api.delete<void>(`/webhooks/${id}`),
  newSecret: (id: number) => api.post<{ webhook: Webhook; secret: string }>(`/webhooks/${id}/secret`),
  deliveries: (id: number) => api.get<{ items: WebhookDelivery[] }>(`/webhooks/${id}/deliveries`),
  test: (id: number) => api.post<WebhookDelivery>(`/webhooks/${id}/test`),
}

export const openApi = {
  title: (kind: string, ref: string) => api.get<{ title_id: number | null; artist_id?: number | null }>(`/open/title/${encodeURIComponent(kind)}/${encodeURIComponent(ref)}`),
  version: (versionId: string) => api.get<{ kind: string }>(`/open/version/${encodeURIComponent(versionId)}`),
  download: (downloadId: string) => api.get<{ view: string }>(`/open/download/${encodeURIComponent(downloadId)}`),
}

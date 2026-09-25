import { api } from './client'
import type { MediaServer, MediaServerCreate, MediaServerKind, MediaServerTest, MediaServerTestResult, MediaServerUpdate, PlexChosen, PlexPin, PlexPinState, PlexServers } from './types'

export const MEDIA_SERVER_KINDS: readonly MediaServerKind[] = ['plex', 'jellyfin', 'emby']

/**
 * Plex, Jellyfin und Emby. Der Token oder API-Schluessel geht nur hinaus, nie herein: Antworten tragen `has_token`.
 * Speichern prueft die Verbindung und liest die Bibliotheken.
 *
 * ⚠️ Nur `plexPin`, `plexPinState` und `plexServers` loesen einen Kontakt zu plex.tv aus, und nur hinter dem Knopf
 * "Bei plex.tv anmelden". Wer den Token von Hand eintraegt, ruft keine der drei. `plexChoose` fragt nur den Server
 * selbst, an seinen eigenen Adressen.
 */
export const mediaServersApi = {
  list: () => api.get<MediaServer[]>('/media-servers'),
  /** 201. 422 `mediaserver_invalid`, 409 `plextv_pin_unclaimed`, 502 `mediaserver_unreachable`, `mediaserver_auth_failed`, `mediaserver_wrong_kind`, `mediaserver_http_error`. */
  create: (body: MediaServerCreate) => api.post<MediaServer>('/media-servers', body),
  /** Eine neue Adresse oder ein neuer Token wird geprueft. Der Server aendert sich nie (422 `mediaserver_kind_locked`). */
  update: (id: number, body: MediaServerUpdate) => api.patch<MediaServer>(`/media-servers/${id}`, body),
  remove: (id: number) => api.delete<void>(`/media-servers/${id}`),
  /** Prueft, ohne zu speichern. Mit `id` nimmt der Server ohne `token` den gespeicherten. */
  test: (body: MediaServerTest) => api.post<MediaServerTestResult>('/media-servers/test', body),
  /** Prueft den gespeicherten Server und liest seine Bibliotheken neu; die Schalter bleiben. */
  check: (id: number) => api.post<MediaServer>(`/media-servers/${id}/check`, undefined),
  plexPin: () => api.post<PlexPin>('/media-servers/plex/pin', undefined),
  plexPinState: (pinId: number) => api.get<PlexPinState>(`/media-servers/plex/pin/${pinId}`),
  /** Die eigenen Server des angemeldeten Kontos. 409 `plextv_pin_unclaimed`, 502 `plextv_error`. */
  plexServers: (pinId: number) => api.get<PlexServers>(`/media-servers/plex/pin/${pinId}/servers`),
  /** Sucht den Server an seinen Adressen, die lokalen zuerst. 404 `plextv_server_unknown`, 502 `mediaserver_unreachable`. */
  plexChoose: (pinId: number, machineId: string) =>
    api.post<PlexChosen>(`/media-servers/plex/pin/${pinId}/servers/${encodeURIComponent(machineId)}`, undefined),
}

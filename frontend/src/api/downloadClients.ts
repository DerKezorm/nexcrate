import { api } from './client'
import type {
  DownloadClient,
  DownloadClientCreate,
  DownloadClientKind,
  DownloadClientTest,
  DownloadClientTestResult,
  DownloadClientUpdate,
  DownloadProtocol,
  RadarrDownloadClient,
} from './types'

export const DOWNLOAD_CLIENT_KINDS: readonly DownloadClientKind[] = ['sabnzbd', 'nzbget', 'qbittorrent', 'transmission', 'deluge']

/** Die eigene Kategorie, solange niemand eine andere nennt. */
export const DEFAULT_CATEGORY = 'nexcrate'

export const CLIENT_PRIORITY_MIN = 1
export const CLIENT_PRIORITY_MAX = 50
export const CLIENT_PRIORITY_DEFAULT = 1

/** 1 bis 64 Zeichen aus Kleinbuchstaben, Ziffern, Punkt, Unterstrich und Bindestrich. SABnzbd fuehrt Kategorien klein (gemessen 14.09.2026). */
export const CATEGORY_PATTERN = /^[a-z0-9._-]{1,64}$/

/** SABnzbd und NZBGet laden aus dem Usenet, qBittorrent, Transmission und Deluge Torrents. */
export function protocolOf(kind: DownloadClientKind): DownloadProtocol {
  return kind === 'sabnzbd' || kind === 'nzbget' ? 'usenet' : 'torrent'
}

/** Mit Benutzername und Passwort melden sich qBittorrent, NZBGet und Transmission an; SABnzbd nimmt einen API-Schluessel,
 * Deluge nur das Passwort seiner Weboberflaeche. */
export function usesUsername(kind: DownloadClientKind): boolean {
  return kind === 'qbittorrent' || kind === 'nzbget' || kind === 'transmission'
}

/**
 * SABnzbd und qBittorrent. Das Geheimnis geht nur hinaus, nie herein: Antworten tragen `has_secret`.
 * Speichern prueft die Verbindung und legt die Kategorie an.
 */
export const downloadClientsApi = {
  list: () => api.get<DownloadClient[]>('/download-clients'),
  /** 201. 422 `client_invalid`, 502 `client_unreachable`, `client_auth_failed`, `client_address_refused`, `client_wrong_kind`, `client_category_failed`. */
  create: (body: DownloadClientCreate) => api.post<DownloadClient>('/download-clients', body),
  /** Eine neue Adresse, Benutzername, Geheimnis oder Kategorie wird geprueft. Das Programm aendert sich nie (422 `client_kind_locked`). */
  update: (id: number, body: DownloadClientUpdate) => api.patch<DownloadClient>(`/download-clients/${id}`, body),
  /** 409 `client_in_use`, solange Downloads laufen. */
  remove: (id: number) => api.delete<void>(`/download-clients/${id}`),
  test: (body: DownloadClientTest) => api.post<DownloadClientTestResult>('/download-clients/test', body),
  /**
   * Die SABnzbd- und qBittorrent-Eintraege eines Radarr. Dieselbe Route liest auch ein Sonarr (S6, Entscheidung 14);
   * `radarr_category` ist dort dessen Kategorie fuer Serien.
   */
  fromRadarr: (sourceId: number) => api.get<RadarrDownloadClient[]>(`/download-clients/from-radarr?source_id=${sourceId}`),
}

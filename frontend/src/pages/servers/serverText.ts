/**
 * Die Saetze zu Medienservern. Die Schluessel stehen woertlich da, damit `keys.test.ts` sie sieht.
 */

import type { TFunction } from 'i18next'

import type { MediaServerLibrary } from '../../api/types'

/** Kein Fehler, sondern ein Zustand: Der letzte Ordner lag in keiner Bibliothek, es fehlt wohl eine Pfadzuordnung. */
export const PATH_UNMATCHED = 'mediaserver_path_unmatched'

/** Die Codes, die als letzter Fehler an einem Server stehen koennen. Eigene Saetze ohne Werte. */
export const SERVER_ERROR_CODES = ['mediaserver_unreachable', 'mediaserver_auth_failed', 'mediaserver_wrong_kind', 'mediaserver_http_error'] as const

export function serverKindText(t: TFunction, kind: string): string {
  switch (kind) {
    case 'plex':
      return t('settings.servers.kind.plex')
    case 'jellyfin':
      return t('settings.servers.kind.jellyfin')
    case 'emby':
      return t('settings.servers.kind.emby')
    default:
      return kind
  }
}

export function libraryKindText(t: TFunction, kind: MediaServerLibrary['kind']): string {
  switch (kind) {
    case 'movie':
      return t('settings.servers.libraries.kind.movie')
    case 'series':
      return t('settings.servers.libraries.kind.series')
    case 'music':
      return t('settings.servers.libraries.kind.music')
    default:
      return t('settings.servers.libraries.kind.other')
  }
}

export function serverErrorText(t: TFunction, code: string): string {
  switch (code) {
    case 'mediaserver_unreachable':
      return t('settings.servers.lastError.mediaserver_unreachable')
    case 'mediaserver_auth_failed':
      return t('settings.servers.lastError.mediaserver_auth_failed')
    case 'mediaserver_wrong_kind':
      return t('settings.servers.lastError.mediaserver_wrong_kind')
    case 'mediaserver_http_error':
      return t('settings.servers.lastError.mediaserver_http_error')
    default:
      return t('settings.servers.lastError.unknown', { code })
  }
}

/** Wann und wie der Server zuletzt von einer Aenderung erfuhr. */
export function notifyText(t: TFunction, result: string | null, when: string): string {
  switch (result) {
    case 'folder':
      return t('settings.servers.notify.folder', { when })
    case 'library':
      return t('settings.servers.notify.library', { when })
    case 'failed':
      return t('settings.servers.notify.failed', { when })
    default:
      return t('settings.servers.notify.never')
  }
}

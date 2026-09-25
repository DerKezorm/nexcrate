/**
 * Die Saetze zu Download-Programmen. Die Schluessel stehen woertlich da, damit `keys.test.ts` sie sieht.
 */

import type { TFunction } from 'i18next'

import type { DownloadClient } from '../../api/types'
import { formatNumber } from '../../lib/format'

/**
 * Die Codes, die als letzter Fehler an einem Programm stehen koennen. Eigene Saetze ohne Werte,
 * weil die Werte der Meldung (etwa der Grund bei `client_category_unusable`) dort nicht mehr dabei sind.
 */
export const CLIENT_ERROR_CODES = [
  'client_unreachable',
  'client_auth_failed',
  'client_wrong_kind',
  'client_category_failed',
  'client_category_unusable',
  'client_host_refused',
  'client_address_refused',
] as const

export function clientKindText(t: TFunction, kind: string): string {
  switch (kind) {
    case 'sabnzbd':
      return t('settings.clients.kind.sabnzbd')
    case 'qbittorrent':
      return t('settings.clients.kind.qbittorrent')
    case 'nzbget':
      return t('settings.clients.kind.nzbget')
    case 'transmission':
      return t('settings.clients.kind.transmission')
    case 'deluge':
      return t('settings.clients.kind.deluge')
    default:
      return kind
  }
}

/** Der Satz unter der Wahl des Programms. */
export function clientKindHint(t: TFunction, kind: string): string {
  return kind === 'sabnzbd' || kind === 'nzbget'
    ? t('settings.clients.kind.sabnzbdHint')
    : t('settings.clients.kind.qbittorrentHint')
}

/** NZBGet nimmt jede Kategorie an; nexcrate legt dort keine an. */
export function clientCategoryHint(t: TFunction, kind: string): string {
  switch (kind) {
    case 'nzbget':
      return t('settings.clients.form.categoryHintNzbget')
    case 'transmission':
      return t('settings.clients.form.categoryHintTransmission')
    case 'deluge':
      return t('settings.clients.form.categoryHintDeluge')
    default:
      return t('settings.clients.form.categoryHint')
  }
}

export function clientSaveHint(t: TFunction, kind: string): string {
  return kind === 'nzbget' || kind === 'transmission'
    ? t('settings.clients.form.saveHintConnectionOnly')
    : t('settings.clients.form.saveHint')
}

export function clientUrlHint(t: TFunction, kind: string): string {
  switch (kind) {
    case 'qbittorrent':
      return t('settings.clients.form.urlHintQbittorrent')
    case 'nzbget':
      return t('settings.clients.form.urlHintNzbget')
    case 'transmission':
      return t('settings.clients.form.urlHintTransmission')
    case 'deluge':
      return t('settings.clients.form.urlHintDeluge')
    default:
      return t('settings.clients.form.urlHintSabnzbd')
  }
}

export function clientSecretHint(t: TFunction, kind: string): string {
  switch (kind) {
    case 'qbittorrent':
      return t('settings.clients.form.passwordHint')
    case 'nzbget':
      return t('settings.clients.form.passwordHintNzbget')
    case 'transmission':
      return t('settings.clients.form.passwordHintTransmission')
    case 'deluge':
      return t('settings.clients.form.passwordHintDeluge')
    default:
      return t('settings.clients.form.apiKeyHint')
  }
}

/** Die Aufbewahrung der Newsserver, wie nexcrate sie aus dem Programm gelesen hat. */
export function retentionText(t: TFunction, client: Pick<DownloadClient, 'retention' | 'retention_days'>, language: string): string {
  switch (client.retention) {
    case 'days':
      return t('settings.clients.details.retentionDays', {
        count: client.retention_days ?? 0,
        value: formatNumber(client.retention_days ?? 0, language),
      })
    case 'unlimited':
      return t('settings.clients.details.retentionUnlimited')
    default:
      return t('settings.clients.details.retentionUnknown')
  }
}

export function protocolText(t: TFunction, protocol: string): string {
  switch (protocol) {
    case 'usenet':
      return t('settings.clients.protocol.usenet')
    case 'torrent':
      return t('settings.clients.protocol.torrent')
    default:
      return protocol
  }
}

export function clientErrorText(t: TFunction, code: string): string {
  switch (code) {
    case 'client_unreachable':
      return t('settings.clients.lastError.client_unreachable')
    case 'client_auth_failed':
      return t('settings.clients.lastError.client_auth_failed')
    case 'client_wrong_kind':
      return t('settings.clients.lastError.client_wrong_kind')
    case 'client_category_failed':
      return t('settings.clients.lastError.client_category_failed')
    case 'client_category_unusable':
      return t('settings.clients.lastError.client_category_unusable')
    case 'client_host_refused':
      return t('settings.clients.lastError.client_host_refused')
    case 'client_address_refused':
      return t('settings.clients.lastError.client_address_refused')
    default:
      return t('settings.clients.lastError.unknown', { code })
  }
}

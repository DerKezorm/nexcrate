import type { TFunction } from 'i18next'

import type { BlocklistEntry } from '../../api/types'

/** Warum ein Release gesperrt ist. Die Gruende kommen als Codes; ein unbekannter bleibt lesbar. */
export function blockReasonText(t: TFunction, reason: string): string {
  switch (reason) {
    case 'client_failed':
      return t('title.blocklist.reason.client_failed')
    case 'encrypted':
      return t('title.blocklist.reason.encrypted')
    case 'dangerous_file':
      return t('title.blocklist.reason.dangerous_file')
    case 'multi_part':
      return t('title.blocklist.reason.multi_part')
    case 'removed_by_owner':
      return t('title.blocklist.reason.removed_by_owner')
    default:
      return t('title.blocklist.reason.unknown', { code: reason })
  }
}

/**
 * Warum ein Download fehlgeschlagen ist, im Verlauf des Titels: Dort steht der Grund als Code im Detail eines
 * `failed`. Die bekannten Gruende sagen dasselbe wie in der Sperrliste. Ein unbekannter bekommt einen allgemeinen
 * Satz, nie den rohen Code (gefunden in der Sichtpruefung gegen den echten Server, 14.09.2026).
 */
export function failureReasonText(t: TFunction, code: string): string {
  switch (code) {
    case 'client_failed':
      return t('title.blocklist.reason.client_failed')
    case 'encrypted':
      return t('title.blocklist.reason.encrypted')
    case 'dangerous_file':
      return t('title.blocklist.reason.dangerous_file')
    default:
      return t('title.history.failedUnknown')
  }
}

/** Ein Eintrag, wie der Server ihn schickt. Alles andere laesst die Seite weg, statt leere Zeilen zu zeigen. */
export function isBlocklistEntry(value: unknown): value is BlocklistEntry {
  if (!value || typeof value !== 'object') return false
  const entry = value as Record<string, unknown>
  return typeof entry.id === 'number' && typeof entry.release_title === 'string' && typeof entry.reason === 'string' && typeof entry.created_at === 'string'
}

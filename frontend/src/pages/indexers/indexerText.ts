import type { TFunction } from 'i18next'

import type { Indexer, IndexerKind } from '../../api/types'
import { formatDateTime, formatTime, isToday } from '../../lib/format'

/**
 * Die Codes, die als letzter Fehler an einem Indexer stehen koennen, wie im Plan unter
 * "Error document at any HTTP status". `indexer_url_invalid` und `indexer_no_categories`
 * fehlen hier: Das sind Eingabefehler beim Speichern, kein Zustand des Indexers.
 */
export const INDEXER_ERROR_CODES = [
  'indexer_key_rejected',
  'indexer_limit_reached',
  'indexer_error',
  'indexer_not_newznab',
  'indexer_wrong_kind',
  'indexer_unreachable',
  'indexer_timeout',
  'indexer_http_error',
  // Seit 2c stehen Codes auch an einem Indexer der Suche, dort kommen diese beiden dazu.
  'indexer_answer_too_large',
  'indexer_key_missing',
] as const

/**
 * Der letzte Fehler eines Indexers als kurzer Satz. Hier fehlen die Werte, die zur Meldung
 * gehoerten (etwa der HTTP-Status), deshalb eigene Saetze statt der aus errors.json.
 * Ein unbekannter Code bleibt lesbar.
 */
export function lastErrorText(t: TFunction, code: string): string {
  switch (code) {
    case 'indexer_key_rejected':
      return t('indexers.lastError.keyRejected')
    case 'indexer_limit_reached':
      return t('indexers.lastError.limitReached')
    case 'indexer_error':
      return t('indexers.lastError.error')
    case 'indexer_not_newznab':
      return t('indexers.lastError.notNewznab')
    case 'indexer_wrong_kind':
      return t('indexers.lastError.wrongKind')
    case 'indexer_unreachable':
      return t('indexers.lastError.unreachable')
    case 'indexer_timeout':
      return t('indexers.lastError.timeout')
    case 'indexer_http_error':
      return t('indexers.lastError.httpError')
    case 'indexer_answer_too_large':
      return t('indexers.lastError.answerTooLarge')
    case 'indexer_key_missing':
      return t('indexers.lastError.keyMissing')
    default:
      return t('indexers.lastError.unknown', { code })
  }
}

export function kindText(t: TFunction, kind: IndexerKind): string {
  return kind === 'torznab' ? t('indexers.kind.torznab') : t('indexers.kind.newznab')
}

/** Bis wann ein Indexer pausiert, oder null, wenn die Pause vorbei ist oder nie war. */
export function pausedUntil(indexer: Indexer, now: number = Date.now()): string | null {
  if (!indexer.paused_until) return null
  const until = new Date(indexer.paused_until).getTime()
  return Number.isFinite(until) && until > now ? indexer.paused_until : null
}

/** Heute nur die Uhrzeit, sonst Datum und Uhrzeit. */
export function momentText(iso: string, language: string): string {
  return isToday(iso) ? formatTime(iso, language) : formatDateTime(iso, language)
}

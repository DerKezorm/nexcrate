/**
 * Die Saetze im Reiter Automatik (Schritt 3c): wann nexcrate bei einem Indexer zuletzt neue Releases gelesen hat, was er
 * an Anfragen meldet, welches Tageslimit gilt und ob die Automatik dort Pause macht. Die Schluessel stehen woertlich da,
 * damit `keys.test.ts` sie sieht.
 */

import type { TFunction } from 'i18next'

import type { Indexer, IndexerUsage } from '../../api/types'
import { formatNumber } from '../../lib/format'
import { whenText } from '../../lib/when'

/** Eine Zahl, die der Server wirklich geschickt hat, oder null. */
function amount(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
}

/** Ein Zeitpunkt, der noch nicht vorbei ist, oder null. */
export function stillAhead(iso: string | null | undefined, now: number = Date.now()): string | null {
  if (typeof iso !== 'string' || iso === '') return null
  const time = new Date(iso).getTime()
  return Number.isFinite(time) && time > now ? iso : null
}

/** "Neue Releases zuletzt gelesen heute um 08:15." oder dass noch nie gelesen wurde. */
export function rssLastText(t: TFunction, indexer: Indexer, language: string): string {
  const last = indexer.rss?.last_at
  return typeof last === 'string' && last !== '' ? t('settings.automatic.indexers.rssLast', { when: whenText(t, last, language) }) : t('settings.automatic.indexers.rssNever')
}

/** Eine Luecke beim Lesen neuer Releases, oder null. */
export function rssGapText(t: TFunction, indexer: Indexer, language: string): string | null {
  const gap = indexer.rss?.gap_at
  return typeof gap === 'string' && gap !== '' ? t('settings.automatic.indexers.rssGap', { when: whenText(t, gap, language) }) : null
}

/** Anfragen und Downloads von heute, soweit der Indexer sie meldet: "123 von 1.000 Anfragen heute". Leer ohne Meldung. */
export function usageTexts(t: TFunction, usage: IndexerUsage | null | undefined, language: string): string[] {
  if (!usage) return []
  const texts: string[] = []
  const apiCurrent = amount(usage.api_current)
  const apiMax = amount(usage.api_max)
  if (apiCurrent !== null) {
    texts.push(
      apiMax !== null
        ? t('settings.automatic.indexers.requests', { count: apiMax, current: formatNumber(apiCurrent, language), max: formatNumber(apiMax, language) })
        : t('settings.automatic.indexers.requestsNoMax', { count: apiCurrent, value: formatNumber(apiCurrent, language) }),
    )
  }
  const grabCurrent = amount(usage.grab_current)
  const grabMax = amount(usage.grab_max)
  if (grabCurrent !== null) {
    texts.push(
      grabMax !== null
        ? t('settings.automatic.indexers.grabs', { count: grabMax, current: formatNumber(grabCurrent, language), max: formatNumber(grabMax, language) })
        : t('settings.automatic.indexers.grabsNoMax', { count: grabCurrent, value: formatNumber(grabCurrent, language) }),
    )
  }
  if (texts.length > 0 && typeof usage.seen_at === 'string' && usage.seen_at !== '') {
    texts.push(t('settings.automatic.indexers.seen', { when: whenText(t, usage.seen_at, language) }))
  }
  return texts
}

/** Ist ein gemeldetes Limit erreicht, sagt das und bis wann. Leer, solange noch etwas frei ist. */
export function usedUpTexts(t: TFunction, usage: IndexerUsage | null | undefined, language: string, now: number = Date.now()): string[] {
  if (!usage) return []
  const texts: string[] = []
  const apiCurrent = amount(usage.api_current)
  const apiMax = amount(usage.api_max)
  if (apiCurrent !== null && apiMax !== null && apiCurrent >= apiMax) {
    const next = stillAhead(usage.api_next_at, now)
    texts.push(next !== null ? t('settings.automatic.indexers.requestsUsedUpUntil', { when: whenText(t, next, language) }) : t('settings.automatic.indexers.requestsUsedUp'))
  }
  const grabCurrent = amount(usage.grab_current)
  const grabMax = amount(usage.grab_max)
  if (grabCurrent !== null && grabMax !== null && grabCurrent >= grabMax) {
    const next = stillAhead(usage.grab_next_at, now)
    texts.push(next !== null ? t('settings.automatic.indexers.grabsUsedUpUntil', { when: whenText(t, next, language) }) : t('settings.automatic.indexers.grabsUsedUp'))
  }
  return texts
}

/**
 * Welches Limit gilt, wenn der Indexer keines meldet: das selbst eingetragene Tageslimit, sonst hoechstens eine geplante
 * Anfrage alle zwei Minuten (Entscheidung 7 im Plan). null, wenn der Indexer sein Limit selbst nennt.
 */
export function limitSourceText(t: TFunction, indexer: Indexer, language: string): string | null {
  if (amount(indexer.usage?.api_max) !== null) return null
  const own = amount(indexer.daily_limit)
  if (own !== null && own > 0) return t('settings.automatic.indexers.ownLimit', { count: own, value: formatNumber(own, language) })
  return t('settings.automatic.indexers.noLimit')
}

/** Die Pause nur der Automatik nach Fehlern, solange sie dauert, oder null. */
export function automaticPauseText(t: TFunction, indexer: Indexer, language: string, now: number = Date.now()): string | null {
  const until = stillAhead(indexer.automatic_paused_until, now)
  return until !== null ? t('settings.automatic.indexers.paused', { when: whenText(t, until, language) }) : null
}

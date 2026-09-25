/**
 * Die Saetze zur automatischen Suche eines Titels (Schritt 3c, C8): warum die naechste Suche dann kommt, womit der Plan
 * rechnet und warum das beste Release einer Fassung nicht geladen wurde. Der Server schickt Codes und Arten, nie Saetze.
 * Die Schluessel stehen woertlich da, damit `keys.test.ts` sie sieht.
 */

import type { TFunction } from 'i18next'

import { ApiError, errorText } from '../../api/client'
import type { SearchAnchor } from '../../api/types'
import { formatCalendarDate } from '../../lib/format'
import { lastErrorText } from '../indexers/indexerText'

/** Die Gruende aus dem Plan unter C8. Jeder hat einen eigenen Satz. */
/** Seit dem 22.09.2026 dazu `wish`: ein anderes Programm hat die Suche angefragt, sie laeuft sofort. */
export const PLAN_REASONS = ['anchor', 'schedule', 'limit', 'replacement', 'replacement_limit', 'no_date', 'nothing_wanted', 'off', 'wish'] as const

/** Warum die naechste Suche dann kommt, oder warum keine, in einem Satz. Ein unbekannter Code bleibt lesbar. */
export function planReasonText(t: TFunction, reason: string): string {
  switch (reason) {
    case 'anchor':
      return t('title.automatic.reason.anchor')
    case 'schedule':
      return t('title.automatic.reason.schedule')
    case 'limit':
      return t('title.automatic.reason.limit')
    case 'replacement':
      return t('title.automatic.reason.replacement')
    case 'replacement_limit':
      return t('title.automatic.reason.replacement_limit')
    case 'no_date':
      return t('title.automatic.reason.no_date')
    case 'nothing_wanted':
      return t('title.automatic.reason.nothing_wanted')
    case 'off':
      return t('title.automatic.reason.off')
    case 'wish':
      return t('title.automatic.reason.wish')
    default:
      return t('title.automatic.reason.unknown', { code: reason })
  }
}

/** Die Ablehnungen mit einer kurzen Wendung im Ergebnis je Fassung. Die Suche von Hand zaehlt dieselben Codes. */
export const REJECTION_PHRASE_CODES = [
  'quality_not_allowed',
  'score_below_minimum',
  'too_small',
  'too_large',
  'language_missing',
  'hardcoded_subs',
  'unknown_quality',
  'not_enough_seeders',
  'older_than_retention',
  'blocklisted',
] as const

/**
 * Warum das beste Release einer Fassung nicht passt, als kurze Wendung ohne Zahl, etwa "Zu wenig Punkte". Die Suche von
 * Hand zaehlt Releases je Grund ("12 Releases, die zu groß sind"); hier geht es um ein einziges Release. Ein unbekannter
 * Code bleibt lesbar.
 */
export function rejectionPhrase(t: TFunction, code: string): string {
  switch (code) {
    case 'quality_not_allowed':
      return t('title.automatic.summary.rejection.quality_not_allowed')
    case 'score_below_minimum':
      return t('title.automatic.summary.rejection.score_below_minimum')
    case 'too_small':
      return t('title.automatic.summary.rejection.too_small')
    case 'too_large':
      return t('title.automatic.summary.rejection.too_large')
    case 'language_missing':
      return t('title.automatic.summary.rejection.language_missing')
    case 'hardcoded_subs':
      return t('title.automatic.summary.rejection.hardcoded_subs')
    case 'unknown_quality':
      return t('title.automatic.summary.rejection.unknown_quality')
    case 'not_enough_seeders':
      return t('title.automatic.summary.rejection.not_enough_seeders')
    case 'older_than_retention':
      return t('title.automatic.summary.rejection.older_than_retention')
    case 'protocol_disabled':
      return t('title.automatic.summary.rejection.protocol_disabled')
    case 'blocklisted':
      return t('title.automatic.summary.rejection.blocklisted')
    case 'already_imported':
      return t('title.automatic.summary.rejection.already_imported')
    default:
      return t('title.automatic.summary.rejection.unknown', { code })
  }
}

/** Ein Land als Name in der Sprache der Oberflaeche, etwa "Deutschland" fuer DE. Ein Code, den der Browser nicht kennt, steht selbst da. */
export function countryName(code: string, language: string): string {
  const upper = code.trim().toUpperCase()
  try {
    const name = new Intl.DisplayNames([language], { type: 'region' }).of(upper)
    if (name && name.toUpperCase() !== upper) return name
  } catch {
    // Kein gueltiger Code fuer ein Land.
  }
  return upper
}

function yearOf(day: string | null): number | null {
  const match = typeof day === 'string' ? /^(\d{4})/.exec(day) : null
  return match ? Number(match[1]) : null
}

/**
 * Womit der Plan rechnet, etwa "Digitale Veröffentlichung in Deutschland am 20.08.2026". Ohne Land ist es der frueheste
 * Tag in irgendeinem Land. Beim Kinostart ist das Datum schon mit den 90 Tagen gerechnet. Fehlt, was ein Satz braucht,
 * heisst es "Kein Erscheinungsdatum bekannt".
 */
export function anchorText(t: TFunction, anchor: SearchAnchor, language: string, titleYear: number | null): string {
  const date = typeof anchor.date === 'string' && anchor.date !== '' ? formatCalendarDate(anchor.date, language) : null
  const country = typeof anchor.country === 'string' && anchor.country.trim() !== '' ? countryName(anchor.country, language) : null
  if (date !== null) {
    switch (anchor.kind) {
      case 'digital':
        return country !== null ? t('title.automatic.anchorKind.digital', { country, date }) : t('title.automatic.anchorKind.digitalAny', { date })
      case 'physical':
        return country !== null ? t('title.automatic.anchorKind.physical', { country, date }) : t('title.automatic.anchorKind.physicalAny', { date })
      case 'theatrical':
        return country !== null ? t('title.automatic.anchorKind.theatrical', { country, date }) : t('title.automatic.anchorKind.theatricalAny', { date })
      // Musik M5: der Tag, an dem das Album erschienen ist oder erscheint.
      case 'release':
        return t('title.automatic.anchorKind.release', { date })
    }
  }
  if (anchor.kind === 'year') {
    const year = yearOf(anchor.date) ?? (titleYear !== null && titleYear > 0 ? titleYear : null)
    if (year !== null) return t('title.automatic.anchorKind.year', { year: String(year) })
  }
  if (date !== null && anchor.kind !== 'none' && anchor.kind !== 'year') return t('title.automatic.anchorKind.other', { date })
  return t('title.automatic.anchorKind.none')
}

/** Warum ein Indexer bei der letzten Suche nichts geliefert hat. Die Saetze ohne Werte, wie bei der Suche von Hand. */
export function indexerCodeText(t: TFunction, code: string): string {
  return code === 'internal_error' ? t('search.indexers.internalError') : lastErrorText(t, code)
}

/** Fehler beim Laden, deren Satz in errors.json ohne Werte auskommt. */
const PLAIN_LOAD_ERRORS: readonly string[] = ['no_client_for_protocol', 'release_fetch_failed', 'release_file_invalid', 'client_refused', 'search_expired']

/** Warum nexcrate das beste Release einer Fassung nicht geladen hat, in einem Satz. Ein unbekannter Code bleibt lesbar. */
export function loadCodeText(t: TFunction, code: string, label: string): string {
  switch (code) {
    case 'not_fitting':
    case 'release_not_fitting':
      return t('search.load.confirm.notFitting', { label })
    case 'blocklisted':
    case 'release_blocklisted':
      return t('search.load.confirm.blocklisted')
    case 'version_fed_by_source':
      return t('search.load.block.version_fed_by_source', { label })
    case 'version_no_profile':
      return t('search.load.block.version_no_profile', { label })
    case 'version_no_folder':
      return t('search.load.block.version_no_folder', { label })
    case 'download_active':
      return t('search.load.block.download_active', { label })
    // Die Verzoegerungsregel der Fassung laesst das beste Release noch warten; es steht unter Downloads, "Wartet".
    case 'waiting_for_delay':
      return t('search.load.block.waiting_for_delay', { label })
    default:
      return PLAIN_LOAD_ERRORS.includes(code) ? errorText(t, new ApiError(409, code)) : t('search.load.block.unknown', { code })
  }
}

/** Warum die naechste Suche einer Staffel dann kommt. Serien haben eigene Saetze, der Rest gilt wie bei Filmen. */
export function seasonReasonText(t: TFunction, reason: string): string {
  switch (reason) {
    case 'schedule':
      return t('title.automatic.series.reason.schedule')
    case 'air_date':
      return t('title.automatic.series.reason.air_date')
    case 'no_date':
      return t('title.automatic.series.reason.no_date')
    case 'replacement':
      return t('title.automatic.series.reason.replacement')
    case 'replacement_limit':
      return t('title.automatic.series.reason.replacement_limit')
    default:
      return planReasonText(t, reason)
  }
}

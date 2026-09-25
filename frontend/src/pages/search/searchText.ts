/**
 * Die Saetze zur Suche je Film. Der Server schickt Zustaende, Gruende und Fehler als Codes, nie als
 * Saetze; hier werden sie zu Text. Die Schluessel stehen woertlich da, damit `keys.test.ts` sie sieht.
 */

import type { TFunction } from 'i18next'

import { ApiError, errorText } from '../../api/client'
import type { ReasonCount, SearchIndexer, SearchQuery, SearchRelease } from '../../api/types'
import { formatNumber } from '../../lib/format'
import { lastErrorText } from '../indexers/indexerText'

export function indexerStateText(t: TFunction, state: string): string {
  switch (state) {
    case 'waiting':
      return t('search.indexers.state.waiting')
    case 'searching':
      return t('search.indexers.state.searching')
    case 'done':
      return t('search.indexers.state.done')
    case 'failed':
      return t('search.indexers.state.failed')
    case 'timeout':
      return t('search.indexers.state.timeout')
    case 'skipped':
      return t('search.indexers.state.skipped')
    default:
      return state
  }
}

/** Seit S3: ein Indexer, den eine Suche nach einer Serie uebersprungen hat. Kein Fehler, aber sichtbar. */
export function isSkipped(indexer: SearchIndexer): boolean {
  return indexer.state === 'skipped'
}

/** Warum ein Indexer uebersprungen wurde. Ohne Serienkategorien hat es einen eigenen Satz. */
export function skippedText(t: TFunction, indexer: SearchIndexer): string {
  if (indexer.error_code === 'indexer_no_music_categories') return t('search.indexers.skippedMusicCategories')
  if (indexer.error_code === null || indexer.error_code === 'indexer_no_series_categories') return t('search.indexers.skippedCategories')
  return t('search.indexers.skippedOther', { text: lastErrorText(t, indexer.error_code) })
}

/** Ein Indexer, der nichts oder nicht alles geliefert hat. */
export function isFailed(indexer: SearchIndexer): boolean {
  return indexer.state === 'failed' || indexer.state === 'timeout'
}

/** Warum ein Indexer nichts geliefert hat. Die Saetze ohne Werte aus dem Reiter Indexer, weil die Suche keine Werte mitschickt. */
export function failureText(t: TFunction, indexer: SearchIndexer): string {
  if (indexer.state === 'timeout' && (indexer.error_code === null || indexer.error_code === 'indexer_timeout')) return t('search.indexers.timeout')
  // Der Server nennt so einen Fehler in nexcrate selbst, dessen Einzelheiten im Protokoll stehen.
  if (indexer.error_code === 'internal_error') return t('search.indexers.internalError')
  return indexer.error_code !== null ? lastErrorText(t, indexer.error_code) : t('indexers.lastError.error')
}

export function queryText(t: TFunction, query: SearchQuery): string {
  if (query.kind === 'id') return query.text === '' || query.text === 'ids' ? t('search.indexers.queryId') : t('search.indexers.queryIdWith', { text: query.text })
  return t('search.indexers.queryTitle', { text: query.text })
}

export function releaseCountText(t: TFunction, count: number, language: string): string {
  return t('search.indexers.releases', { count, value: formatNumber(count, language) })
}

/** Ein haeufiger Grund mit der Zahl der Releases, etwa "12 Releases, die zu groß sind". */
export function reasonCountText(t: TFunction, reason: ReasonCount, language: string): string {
  const values = { count: reason.count, value: formatNumber(reason.count, language) }
  switch (reason.code) {
    case 'quality_not_allowed':
      return t('search.reasons.quality_not_allowed', values)
    case 'score_below_minimum':
      return t('search.reasons.score_below_minimum', values)
    case 'too_small':
      return t('search.reasons.too_small', values)
    case 'too_large':
      return t('search.reasons.too_large', values)
    case 'language_missing':
      return t('search.reasons.language_missing', values)
    case 'hardcoded_subs':
      return t('search.reasons.hardcoded_subs', values)
    case 'unknown_quality':
      return t('search.reasons.unknown_quality', values)
    case 'not_enough_seeders':
      return t('search.reasons.not_enough_seeders', values)
    case 'older_than_retention':
      return t('search.reasons.older_than_retention', values)
    case 'protocol_disabled':
      return t('search.reasons.protocol_disabled', values)
    // Seit Schritt 3: Ein gesperrtes Release nimmt nexcrate nie, der Server zaehlt es unter diesem Grund.
    case 'blocklisted':
      return t('search.reasons.blocklisted', values)
    // Seit S3 die Gruende des Serien-Pruefers.
    case 'refused_form':
      return t('search.reasons.refused_form', values)
    case 'no_episode_match':
      return t('search.reasons.no_episode_match', values)
    case 'season_incomplete':
      return t('search.reasons.season_incomplete', values)
    case 'already_imported':
      return t('search.reasons.already_imported', values)
    default:
      return t('search.reasons.unknown', { ...values, code: reason.code })
  }
}

/**
 * Warum die Suche nicht starten konnte. `search_busy`, `no_indexers` und `search_running` kommen wie
 * ueberall aus errors.json, damit es fuer einen Fall nur einen Satz gibt. Eigene Saetze haben nur
 * die Faelle, deren allgemeiner Text hier nichts sagt: kein Film, Titel weg. Bei einer Serie heisst 422, dass der
 * Umfang (Staffel, Folge) nicht mehr passt; `anime_later` kommt aus errors.json.
 */
export function startProblemText(t: TFunction, error: unknown, series = false): string {
  if (error instanceof ApiError) {
    if (error.status === 422 && error.code === 'invalid_input') return series ? t('search.series.problems.scope') : t('search.problems.notMovie')
    if (error.status === 404 && error.code === 'not_found') return t('search.problems.titleGone')
  }
  return errorText(t, error)
}

/** Beim Nachfragen heisst 404: Die Suche ist abgelaufen. */
export function pollProblemText(t: TFunction, error: unknown): string {
  if (error instanceof ApiError && error.status === 404) return t('search.problems.expired')
  return errorText(t, error)
}

/** Torrents zeigen Seeder, Usenet zeigt Grabs. */
export function isTorrent(release: SearchRelease): boolean {
  if (release.protocol === 'torrent' || release.protocol === 'torznab') return true
  if (release.protocol === 'usenet' || release.protocol === 'newznab') return false
  return release.seeders !== null
}

export function peersText(t: TFunction, release: SearchRelease, language: string): string | null {
  if (isTorrent(release)) {
    return release.seeders === null ? null : t('search.releases.seeders', { count: release.seeders, value: formatNumber(release.seeders, language) })
  }
  return release.grabs === null ? null : t('search.releases.grabs', { count: release.grabs, value: formatNumber(release.grabs, language) })
}

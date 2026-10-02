import type { TFunction } from 'i18next'

import type { ProwlarrCounts, ProwlarrSyncLevel } from '../../api/types'

/** Die Stufe des Abgleichs in Worten, wie Prowlarr sie nennt. */
export function syncLevelText(t: TFunction, level: ProwlarrSyncLevel): string {
  return level === 'add_remove' ? t('indexers.prowlarr.levelAddRemove') : t('indexers.prowlarr.levelFull')
}

/**
 * Was der letzte Abgleich getan hat, als ein Satz: wie viele Indexer Prowlarr hat, dann nur, was sich geaendert hat und
 * was ausgelassen wurde. Die Schluessel stehen woertlich da, damit `keys.test.ts` sie sieht.
 */
export function countsText(t: TFunction, counts: ProwlarrCounts): string {
  const parts = [t('indexers.prowlarr.counts.total', { count: counts.total })]
  if (counts.added > 0) parts.push(t('indexers.prowlarr.counts.added', { count: counts.added }))
  if (counts.updated > 0) parts.push(t('indexers.prowlarr.counts.updated', { count: counts.updated }))
  if (counts.removed > 0) parts.push(t('indexers.prowlarr.counts.removed', { count: counts.removed }))
  if (counts.adopted > 0) parts.push(t('indexers.prowlarr.counts.adopted', { count: counts.adopted }))
  if (counts.skipped_categories > 0) parts.push(t('indexers.prowlarr.counts.skippedCategories', { count: counts.skipped_categories }))
  if (counts.skipped_disabled > 0) parts.push(t('indexers.prowlarr.counts.skippedDisabled', { count: counts.skipped_disabled }))
  if (counts.skipped_tags > 0) parts.push(t('indexers.prowlarr.counts.skippedTags', { count: counts.skipped_tags }))
  if (counts.skipped_unsupported > 0) parts.push(t('indexers.prowlarr.counts.skippedUnsupported', { count: counts.skipped_unsupported }))
  if (counts.kept_blocked > 0) parts.push(t('indexers.prowlarr.counts.keptBlocked', { count: counts.kept_blocked }))
  return parts.join(', ') + '.'
}

/** Der Fehler des letzten Abgleichs. Die Saetze ohne Werte, weil an der Verbindung nur der Code steht. */
export function prowlarrErrorText(t: TFunction, code: string): string {
  switch (code) {
    case 'prowlarr_unreachable':
      return t('indexers.prowlarr.errors.unreachable')
    case 'prowlarr_timeout':
      return t('indexers.prowlarr.errors.timeout')
    case 'prowlarr_key_rejected':
      return t('indexers.prowlarr.errors.keyRejected')
    case 'prowlarr_not_prowlarr':
      return t('indexers.prowlarr.errors.notProwlarr')
    case 'prowlarr_too_old':
      return t('indexers.prowlarr.errors.tooOld')
    case 'prowlarr_key_missing':
      return t('indexers.prowlarr.errors.keyMissing')
    case 'prowlarr_tag_missing':
      return t('indexers.prowlarr.errors.tagMissing')
    default:
      return t('indexers.prowlarr.errors.other', { code })
  }
}

/** The words of the companion files (`release.nex`), shared by the settings section and the takeover's result. */

import type { TFunction } from 'i18next'

import type { CompanionJob } from '../../api/types'
import { formatNumber } from '../../lib/format'

/** The states of the report in the order they are listed: what needs the owner first, what is fine last. */
export const REPORT_ORDER: readonly string[] = [
  'not_writable',
  'missing',
  'outdated',
  'foreign',
  'broken',
  'newer_format',
  'other_installation',
  'changed',
  'file_missing',
  'folder_missing',
  'no_space',
  'failed',
  'written',
  'current',
]

/** The states "Ersetzen" is allowed for (L3): a file nexcrate would never overwrite on its own. */
export const REPLACEABLE = new Set(['foreign', 'broken', 'newer_format', 'other_installation', 'changed'])

/** One count of the report as a short sentence, "{{count}} fehlen". A state this interface does not know names its code. */
export function companionCountText(t: TFunction, state: string, count: number, language: string): string {
  const values = { count, value: formatNumber(count, language) }
  switch (state) {
    case 'current':
      return t('settings.files.companions.count.current', values)
    case 'written':
      return t('settings.files.companions.count.written', values)
    case 'missing':
      return t('settings.files.companions.count.missing', values)
    case 'outdated':
      return t('settings.files.companions.count.outdated', values)
    case 'other_installation':
      return t('settings.files.companions.count.other_installation', values)
    case 'newer_format':
      return t('settings.files.companions.count.newer_format', values)
    case 'broken':
      return t('settings.files.companions.count.broken', values)
    case 'foreign':
      return t('settings.files.companions.count.foreign', values)
    case 'changed':
      return t('settings.files.companions.count.changed', values)
    case 'not_writable':
      return t('settings.files.companions.count.not_writable', values)
    case 'file_missing':
      return t('settings.files.companions.count.file_missing', values)
    case 'folder_missing':
      return t('settings.files.companions.count.folder_missing', values)
    case 'no_space':
      return t('settings.files.companions.count.no_space', values)
    case 'failed':
      return t('settings.files.companions.count.failed', values)
    case 'shared':
      return t('settings.files.companions.count.shared', values)
    default:
      return t('settings.files.companions.count.other', { ...values, code: state })
  }
}

/** The counts of a report or a takeover's phase as rows, known states in their order, unknown ones behind, zeros left out. */
export function companionCountRows(counts: Record<string, unknown> | null | undefined): { state: string; count: number }[] {
  if (!counts || typeof counts !== 'object') return []
  const entries = Object.entries(counts).filter((entry): entry is [string, number] => typeof entry[1] === 'number' && entry[1] > 0)
  const rank = (state: string) => {
    const index = REPORT_ORDER.indexOf(state)
    return index < 0 ? REPORT_ORDER.length : index
  }
  return entries.sort((left, right) => rank(left[0]) - rank(right[0])).map(([state, count]) => ({ state, count }))
}

/** true when the report found every file present and current, so the list would only say "0 fehlen". */
export function allCurrent(job: CompanionJob): boolean {
  const rows = companionCountRows(job.result?.counts)
  return rows.length > 0 && rows.every((row) => row.state === 'current' || row.state === 'written')
}

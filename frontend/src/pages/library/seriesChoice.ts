import type { SeriesVersionChoice, WatchRule } from '../../api/types'

/**
 * Welche Serienfassungen beim letzten Hinzufuegen gewaehlt waren, je mit ihrer Regel. Getrennt von
 * den Filmen (`nexcrate.addVersions`), weil Serienfassungen andere Nummern haben.
 *
 * ⚠️ Jeder Zugriff in try/catch: Im privaten Modus oder bei gesperrtem Speicher wirft schon
 * `localStorage.getItem`. Dann gilt die Wahl nur fuer diesen Dialog.
 */
export const ADD_SERIES_VERSIONS_KEY = 'nexcrate.addSeriesVersions'

export const WATCH_RULES: readonly WatchRule[] = ['all', 'future', 'missing', 'from_season', 'none']

function isRule(value: unknown): value is WatchRule {
  return WATCH_RULES.some((rule) => rule === value)
}

export function readSeriesChoice(): SeriesVersionChoice[] | null {
  try {
    const raw = localStorage.getItem(ADD_SERIES_VERSIONS_KEY)
    if (raw === null) return null
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return null
    return parsed.flatMap((entry: unknown) => {
      if (!entry || typeof entry !== 'object') return []
      const { version_id: id, rule, from_season: from } = entry as Record<string, unknown>
      if (!Number.isInteger(id) || !isRule(rule)) return []
      return [{ version_id: id as number, rule, from_season: Number.isInteger(from) && (from as number) >= 1 ? (from as number) : null }]
    })
  } catch {
    return null
  }
}

export function storeSeriesChoice(choices: readonly SeriesVersionChoice[]): void {
  try {
    localStorage.setItem(ADD_SERIES_VERSIONS_KEY, JSON.stringify([...choices]))
  } catch {
    // Ohne Speicher gilt die Wahl nur bis zum Schliessen.
  }
}

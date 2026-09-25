import type { TFunction } from 'i18next'

import type { ImportRun, ImportRunDetails, Source, SourceApp, SourceTestResult } from '../../api/types'
import { formatNumber } from '../../lib/format'

const MISSING = ' nexcrate-missing-text'

/** Produktnamen, in jeder Sprache gleich. */
export const APP_NAME: Record<SourceApp, string> = { radarr: 'Radarr', sonarr: 'Sonarr', lidarr: 'Lidarr' }

/** Was eine gelungene Verbindungspruefung sagt: App, Version und bei Radarr die Filme, bei Sonarr die Serien. */
export function checkText(t: TFunction, app: SourceApp, tested: SourceTestResult, language: string): string {
  const count = app === 'sonarr' ? (tested.series_count ?? 0) : tested.movie_count
  const value = formatNumber(count, language)
  const amount = app === 'sonarr' ? t('series.import.series', { count, value }) : t('import.count.movies', { count, value })
  return t('import.sources.checkOk', { app: APP_NAME[app], version: tested.app_version, amount })
}

/** Ein fertiger Lauf aus Sonarr traegt seine Zahlen in `details`. Ein Lauf aus Radarr hat keine. */
function seriesDetails(run: ImportRun): ImportRunDetails | null {
  const details = run.details
  return details && typeof details.series === 'number' ? details : null
}

/** Die Zahlen eines Laufs in einem Satz. Bei Sonarr mit der Zahl der gelesenen Serien. */
export function runCounts(t: TFunction, run: ImportRun, language: string): string {
  const number = (value: number) => formatNumber(value, language)
  const details = seriesDetails(run)
  if (details !== null) {
    const series = details.series ?? 0
    return t('series.import.counts', {
      series: t('series.import.series', { count: series, value: number(series) }),
      new: number(run.titles_new),
      updated: number(run.titles_updated),
      removed: number(run.versions_removed),
    })
  }
  return t('import.run.counts', {
    new: number(run.titles_new),
    updated: number(run.titles_updated),
    versions: number(run.versions_total),
    removed: number(run.versions_removed),
  })
}

/**
 * Die Zeilen eines fertigen Laufs. Radarr: die Zahlen. Sonarr: dazu die zugeordneten Folgen, Dateien ohne passende
 * TMDB-Folge und die Serien, die TMDB nicht kennt. Der Server nennt davon hoechstens 20 mit Namen.
 */
export function runLines(t: TFunction, run: ImportRun, language: string): string[] {
  const lines = [runCounts(t, run, language)]
  const details = seriesDetails(run)
  if (details === null) return lines
  const number = (value: number) => formatNumber(value, language)
  if (typeof details.episodes_matched === 'number') {
    lines.push(t('series.import.matched', { matched: number(details.episodes_matched), unmatched: number(details.episodes_unmatched ?? 0) }))
  }
  const filesUnmatched = details.files_unmatched ?? 0
  if (filesUnmatched > 0) lines.push(t('series.import.filesUnmatched', { count: filesUnmatched, value: number(filesUnmatched) }))
  const named = (details.not_on_tmdb ?? []).map((entry) => entry.title)
  const notOnTmdb = Math.max(details.not_on_tmdb_count ?? 0, named.length)
  if (notOnTmdb > 0) {
    const names = named.join(', ') + (notOnTmdb > named.length ? ' …' : '')
    lines.push(t('series.import.notOnTmdb', { count: notOnTmdb, value: number(notOnTmdb), names }))
  }
  return lines
}

/** Wie weit ein laufender Import aus Sonarr ist. Ohne Angaben (Radarr, oder noch nichts gelesen) null. */
export function runProgress(t: TFunction, run: ImportRun, language: string): string | null {
  const details = run.details
  if (run.status !== 'running' || !details || typeof details.done !== 'number' || typeof details.total !== 'number') return null
  const values = { done: formatNumber(details.done, language), total: formatNumber(details.total, language) }
  if (details.phase === 'reading') return t('series.import.reading', values)
  if (details.phase === 'series') return t('series.import.matching', values)
  return null
}

/**
 * Warum ein Lauf scheiterte. Die Werte zum Text kommen mit dem Lauf; die Adresse kennt
 * die Seite zur Not auch von der Quelle. Fehlt ein Wert oder der Text, steht statt
 * eines halben Satzes der Code da.
 */
export function runErrorText(t: TFunction, run: ImportRun, source: Source | undefined): string {
  if (!run.error_code) return t('import.run.failedUnknown')
  const values: Record<string, string | number> = { ...(source ? { url: source.url } : {}), ...(run.error_values ?? {}) }
  const text = t(`errors.${run.error_code}`, { ...values, defaultValue: MISSING })
  if (text === MISSING || text.includes('{{')) return t('import.run.failedCode', { code: run.error_code })
  return text
}

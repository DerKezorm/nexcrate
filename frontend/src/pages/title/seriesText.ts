import type { TFunction } from 'i18next'

import type { SeasonBrief, SeriesBlock, TitleVersion, WatchRule } from '../../api/types'
import { formatNumber } from '../../lib/format'

/** Die Regeln in der Reihenfolge der Auswahl. */
export const WATCH_RULES: readonly WatchRule[] = ['all', 'future', 'missing', 'from_season', 'none']

/** Die Nummer der Fassung unter Einstellungen. Staffeln, Folgen und die Schalter nennen Fassungen nur so. */
export function definitionIdOf(version: TitleVersion): number | null {
  return typeof version.version_id === 'number' ? version.version_id : null
}

/** Eine Fassung, die eine Sonarr-Verbindung fuellt. Hier aendert man nichts an ihr. */
export function isFed(version: TitleVersion): boolean {
  return version.watch?.fed === true
}

/** Der Name einer Regel in der Auswahl. */
export function ruleLabel(t: TFunction, rule: WatchRule): string {
  switch (rule) {
    case 'all':
      return t('series.rule.all')
    case 'future':
      return t('series.rule.future')
    case 'missing':
      return t('series.rule.missing')
    case 'from_season':
      return t('series.rule.from_season')
    case 'none':
      return t('series.rule.none')
  }
}

/** Der Status von TMDB in Worten. Ein unbekannter Status steht da, wie er kommt. */
export function statusText(t: TFunction, status: string | null): string | null {
  if (!status) return null
  switch (status.trim().toLowerCase()) {
    case 'returning series':
      return t('series.status.returning')
    case 'ended':
      return t('series.status.ended')
    case 'canceled':
    case 'cancelled':
      return t('series.status.canceled')
    case 'planned':
      return t('series.status.planned')
    case 'in production':
      return t('series.status.inProduction')
    case 'pilot':
      return t('series.status.pilot')
    default:
      return status
  }
}

function yearOf(day: string | null): number | null {
  const match = typeof day === 'string' ? /^(\d{4})/.exec(day) : null
  return match ? Number(match[1]) : null
}

/** "seit 2020" fuer eine laufende Serie, "2014 bis 2019" fuer eine beendete. */
export function yearsText(t: TFunction, series: SeriesBlock): string | null {
  const from = yearOf(series.first_air_date)
  if (from === null) return null
  const status = (series.status ?? '').trim().toLowerCase()
  const over = status === 'ended' || status === 'canceled' || status === 'cancelled'
  const to = yearOf(series.last_air_date)
  if (over && to !== null) return to === from ? String(from) : t('series.meta.range', { from, to })
  return t('series.meta.since', { year: from })
}

/** Die Staffeln ohne Specials. */
export function regularSeasons(seasons: readonly SeasonBrief[]): SeasonBrief[] {
  return seasons.filter((season) => season.number > 0)
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/** "S01E05", mit Ende "S01E05-E06". */
export function episodeCode(season: number, episode: number, end: number | null = null): string {
  const base = `S${pad(season)}E${pad(episode)}`
  return end !== null && end !== episode ? `${base}-E${pad(end)}` : base
}

/** Was Sonarr fuer eine nicht zugeordnete Datei als Nummern fuehrt: "S01E05E06". null ohne Nummern. */
export function sourceCode(numbers: { season?: number | null; episodes?: number[] } | null): string | null {
  if (!numbers || typeof numbers.season !== 'number') return null
  const episodes = Array.isArray(numbers.episodes) ? numbers.episodes.filter((value) => typeof value === 'number') : []
  if (episodes.length === 0) return null
  return `S${pad(numbers.season)}` + episodes.map((value) => `E${pad(value)}`).join('')
}

/** Folgen einer Staffel im Nummerierungs-Hinweis: "10 Folgen" oder "keine Folge". */
export function numberingEpisodesText(t: TFunction, count: number, language: string): string {
  if (count === 0) return t('series.numbering.none')
  return t('series.numbering.episodes', { count, value: formatNumber(count, language) })
}

/** Woher die Fassung einer Serie kommt. Wie `originText`, aber mit Sonarr statt Radarr. */
export function seriesOriginText(t: TFunction, version: TitleVersion): string {
  const name = version.source_name ?? ''
  if (version.watch?.fed === true || name !== '') {
    return version.added_by === 'owner' ? t('series.origin.ownerFed', { name }) : t('series.origin.import', { name })
  }
  return version.added_by === 'import' ? t('title.origin.importGone') : t('title.origin.owner')
}

/** Was eine Fassung ueberwacht, in einem Satz. Von Hand angepasst steht dahinter. null fuer einen Film. */
export function watchText(t: TFunction, version: TitleVersion): string | null {
  const watch = version.watch
  if (!watch) return null
  if (watch.fed) return t('series.watch.fed', { name: version.source_name ?? '' })
  let text: string | null
  switch (watch.rule) {
    case 'all':
      text = t('series.watch.all')
      break
    case 'future':
      text = t('series.watch.future')
      break
    case 'missing':
      text = t('series.watch.missing')
      break
    case 'from_season':
      text = t('series.watch.fromSeason', { number: watch.from_season ?? 1 })
      break
    case 'none':
      text = t('series.watch.none')
      break
    default:
      text = null
  }
  if (text !== null && watch.custom) return `${text} · ${t('series.watch.custom')}`
  return text
}

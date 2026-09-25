import { useTranslation } from 'react-i18next'

import type { SeriesPreview, Version, WatchRule } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Badge, FormMessage, Toggle } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { SeriesAutomaticNote } from '../title/SeriesAutomaticNote'
import { WATCH_RULES } from './seriesChoice'
import { choiceOf, DEFAULT_PICK, type SeriesPick } from './seriesPreview'

/** Staffeln, Folgen und wie viele gelaufen sind, unter dem Titel der Serie. */
export function SeriesFacts({ preview, counting }: { preview: SeriesPreview | null; counting: boolean }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  if (preview === null) return counting ? <p className="text-sm text-mist-500">{t('series.add.counting')}</p> : null
  return (
    <p className="text-sm wrap-anywhere text-mist-500 tabular-nums">
      {t('series.add.facts', {
        seasons: t('series.meta.seasons', { count: preview.seasons, value: formatNumber(preview.seasons, language) }),
        episodes: t('series.seasons.episodes', { count: preview.episodes, value: formatNumber(preview.episodes, language) }),
        aired: formatNumber(preview.aired, language),
      })}
    </p>
  )
}

const SELECT_CLASS =
  'rounded-full border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60'

type Props = {
  title: string
  versions: Version[]
  /** Die Fassungen, die die Serie schon hat. */
  existing: number[]
  /** Steht die Serie schon in der Bibliothek, kommen Fassungen auf ihrer Seite dazu, nicht hier. */
  inLibrary: boolean
  picks: Record<number, SeriesPick>
  onPick: (versionId: number, pick: SeriesPick) => void
  preview: SeriesPreview | null
  counting: boolean
  daily: boolean
  onDaily: (daily: boolean) => void
  busy: boolean
}

/**
 * Die Wahl der Fassungen fuer eine Serie: je Fassung ein Haken, eine Regel und bei "Ab Staffel" die
 * Staffel. Darunter, wie viele Folgen die Fassung damit ueberwacht, und der Schalter fuer taegliche
 * Sendungen, voreingestellt nach dem Vorschlag von TMDB.
 *
 * Steht die Serie schon da, zeigt der Dialog nur ihre Fassungen: Eine spaeter dazugenommene Fassung
 * ueberwacht beim Server erst einmal alles, die Regel waehlt man auf der Seite der Serie.
 */
export function SeriesChoices({ title, versions, existing, inLibrary, picks, onPick, preview, counting, daily, onDaily, busy }: Props) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const free = versions.filter((version) => !existing.includes(version.id))

  const ruleLabel = (rule: WatchRule): string =>
    ({
      all: t('series.rule.all'),
      future: t('series.rule.future'),
      missing: t('series.rule.missing'),
      from_season: t('series.rule.from_season'),
      none: t('series.rule.none'),
    })[rule]

  if (inLibrary) {
    return (
      <>
        <ul className="flex flex-col gap-2">
          {versions
            .filter((version) => existing.includes(version.id))
            .map((version) => (
              <li key={version.id}>
                <label className="flex cursor-default items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/40 p-3">
                  <input type="checkbox" className="h-4 w-4 shrink-0 accent-accent-500" checked disabled />
                  <span className="flex min-w-0 flex-1 flex-wrap items-center gap-2 font-semibold text-mist-100">
                    <span className="wrap-anywhere">{version.label}</span>
                    <Badge tone="ok">{t('library.add.exists')}</Badge>
                  </span>
                </label>
              </li>
            ))}
        </ul>
        {free.length === 0 && <FormMessage tone="info">{t('series.add.allExist', { title })}</FormMessage>}
      </>
    )
  }

  const seasons = preview?.seasons ?? 0
  const proposedDaily = preview?.proposed_type === 'daily'

  return (
    <>
      <ul className="flex flex-col gap-2">
        {free.map((version) => {
          const pick = picks[version.id] ?? DEFAULT_PICK
          const from = choiceOf(version.id, pick).from_season ?? 1
          const last = Math.max(seasons, from, 1)
          const counted = preview?.versions.find((entry) => entry.version_id === version.id)
          // S6, Entscheidung 31: Der Serienordner liegt schon da. Die Fassung nimmt ihn und liest ihn ein, vor der ersten Suche.
          const onDisk = counted?.on_disk ?? null
          return (
            <li key={version.id}>
              <div className={'flex flex-col gap-2.5 rounded-xl border p-3 ' + (pick.on ? 'border-accent-500/50 bg-accent-500/5' : 'border-ink-700 bg-ink-900/60')}>
                <label className="flex cursor-pointer items-center gap-3">
                  <input
                    type="checkbox"
                    className="h-4 w-4 shrink-0 accent-accent-500"
                    checked={pick.on}
                    disabled={busy}
                    onChange={(event) => onPick(version.id, { ...pick, on: event.target.checked })}
                  />
                  <span className="min-w-0 flex-1 font-semibold wrap-anywhere text-mist-100">{version.label}</span>
                </label>
                <div className="flex flex-wrap items-center gap-2 pl-7">
                  <select
                    value={pick.rule}
                    disabled={!pick.on || busy}
                    aria-label={t('series.add.watchFor', { label: version.label })}
                    onChange={(event) => onPick(version.id, { ...pick, rule: event.target.value as WatchRule })}
                    className={SELECT_CLASS}
                  >
                    {WATCH_RULES.map((rule) => (
                      <option key={rule} value={rule}>
                        {ruleLabel(rule)}
                      </option>
                    ))}
                  </select>
                  {pick.rule === 'from_season' && (
                    <select
                      value={from}
                      disabled={!pick.on || busy}
                      aria-label={t('series.add.fromSeasonFor', { label: version.label })}
                      onChange={(event) => onPick(version.id, { ...pick, from: Number(event.target.value) })}
                      className={SELECT_CLASS}
                    >
                      {Array.from({ length: last }, (_, index) => index + 1).map((number) => (
                        <option key={number} value={number}>
                          {t('series.seasons.season', { number })}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
                {pick.on && (counting || counted) && (
                  <p className="pl-7 text-sm text-mist-400 tabular-nums">
                    {counting || !counted
                      ? t('series.add.counting')
                      : t('series.add.count', { watched: formatNumber(counted.watched, language), aired: formatNumber(counted.aired, language) })}
                  </p>
                )}
                {onDisk !== null && (
                  <p className="flex min-w-0 items-start gap-2 pl-7 text-sm text-mist-200">
                    <Symbol name="folder" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
                    <span className="min-w-0 wrap-anywhere">
                      {t('library.add.onDiskSeries', { count: onDisk.videos, value: formatNumber(onDisk.videos, language), folder: onDisk.folder })}
                    </span>
                  </p>
                )}
              </div>
            </li>
          )
        })}
      </ul>
      <div className="flex flex-col gap-1.5">
        <Toggle label={t('series.daily.label')} hint={t('series.daily.hint')} checked={daily} onChange={onDaily} disabled={busy} />
        {proposedDaily && <p className="pl-7 text-xs text-mist-500">{t('series.daily.proposed')}</p>}
      </div>
      <p className="text-sm text-mist-500">{t('series.add.specials')}</p>
      <SeriesAutomaticNote />
    </>
  )
}

import { Fragment, useEffect, useState } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError, errorText } from '../../api/client'
import { libraryApi } from '../../api/library'
import type { Episode, EpisodeInVersion, SearchStartBody, SeasonBrief, SeasonEpisodes, SeriesBlock, TitleDetail, TitleVersion, VersionState } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Section, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatCalendarDate, formatNumber } from '../../lib/format'
import { DOWNLOADS_PROBLEMS_PATH } from '../downloads/address'
import { STATE_LOOK } from '../../lib/states'
import { versionStateText } from '../../lib/stateText'
import { ownerNumber } from './correction'
import { EpisodePanel } from './EpisodePanel'
import type { DeleteTarget } from './DeleteFilesDialog'
import { InlineSwitch } from './InlineSwitch'
import { definitionIdOf, isFed } from './seriesText'

/** Neueste Staffel oben. Die Specials tragen die Nummer 0 und stehen damit von selbst ganz unten. */
function ordered(seasons: readonly SeasonBrief[]): SeasonBrief[] {
  return [...seasons].sort((a, b) => b.number - a.number)
}

/** Offen beim ersten Blick: die neueste Staffel, von der schon etwas gelaufen ist. */
function firstOpen(seasons: readonly SeasonBrief[]): number[] {
  const newest = ordered(seasons).find((season) => season.number > 0 && season.aired > 0)
  return newest ? [newest.id] : []
}

function seasonName(t: TFunction, season: SeasonBrief): string {
  if (season.number === 0) return t('series.seasons.specials')
  return season.name.trim() !== '' ? season.name : t('series.seasons.season', { number: season.number })
}

function isVersionState(state: string): state is VersionState {
  return Object.prototype.hasOwnProperty.call(STATE_LOOK, state)
}

/** Die Zelle einer Folge in einer Fassung: ein Zustandssymbol, oder ein kurzer Text, wenn ein Symbol nichts sagt. */
function StateCell({ episode, entry, version }: { episode: Episode; entry: EpisodeInVersion | null; version: TitleVersion }) {
  const { t, i18n } = useTranslation()
  if (entry === null) return null
  const settled = entry.state === 'available' || entry.state === 'downloading' || entry.state === 'problem'
  if (!settled && entry.in_source === false) {
    return <span className="text-xs text-mist-500">{t('series.seasons.notInSource', { name: version.source_name ?? '' })}</span>
  }
  if (!settled && entry.watched && !episode.aired) {
    return (
      <span className="text-xs whitespace-nowrap text-mist-500 tabular-nums">
        {episode.air_date ? t('series.seasons.airs', { date: formatCalendarDate(episode.air_date, i18n.language) }) : t('series.seasons.noDate')}
      </span>
    )
  }
  const state = entry.late && !settled ? 'unmonitored' : entry.state
  // Seit S4: ein Download haelt die Folge. Mit Fortschritt, ein Problem mit dem Weg zur Karte (Entscheidung 42).
  if (entry.state === 'downloading' && entry.progress !== null) {
    return (
      <span className="text-xs whitespace-nowrap text-info-400 tabular-nums">{t('series.seasons.downloading', { percent: formatNumber(Math.round(entry.progress), i18n.language) })}</span>
    )
  }
  if (entry.state === 'problem') {
    return (
      <Link to={DOWNLOADS_PROBLEMS_PATH} className="text-xs whitespace-nowrap text-bad-400 hover:underline" onClick={(event) => event.stopPropagation()}>
        {t('series.seasons.problemLink')}
      </Link>
    )
  }
  if (!isVersionState(state)) return <span className="text-xs text-mist-500">{state}</span>
  const look = STATE_LOOK[state]
  const text = t('series.seasons.stateLabel', { version: version.label, state: entry.late && !settled ? t('series.seasons.lateTag') : versionStateText(t, state) })
  return (
    <span className={'inline-flex ' + look.text} title={text}>
      <Symbol name={look.symbol} className="h-4 w-4" />
      <span className="sr-only">{text}</span>
    </span>
  )
}

/**
 * Die Staffeln einer Serie, je Staffel eine aufklappbare Zeile mit den Zahlen je Fassung. Die Folgen einer Staffel
 * laedt die Seite erst beim Aufklappen. `reloadToken` steigt, wenn sich Folgen woanders geaendert haben (neue Regel,
 * nachtraegliche Folgen, neue Fassung); offene Staffeln laden dann neu. `onSwitched` laedt nach einem Schalter die
 * Zahlen des Titels nach.
 *
 * Seit S3 sucht jede Staffel und jede aufgeklappte Folge fuer sich (`onSearch`). `searchBlocked` sperrt die Knoepfe,
 * etwa ohne eingeschalteten Indexer oder bei Anime. Laeuft schon eine Suche, folgt die Seite ihr (409).
 */
export function SeasonList({
  title,
  series,
  reloadToken,
  onSwitched,
  onSearch,
  searchBlocked = false,
  onFilesChanged,
  onDeleteFiles,
}: {
  title: TitleDetail
  series: SeriesBlock
  reloadToken: number
  onSwitched: () => void
  /** Seit 18.09.2026: nach "Datei loesen" die Antwort des Servers, damit Seite und offene Staffeln nachladen. */
  onFilesChanged?: (detail: TitleDetail) => void
  onSearch?: (scope: SearchStartBody) => void
  searchBlocked?: boolean
  /** /api/v1 V2: Dateien einer Staffel oder einer Folge in den Papierkorb; die Seite fragt vorher nach. */
  onDeleteFiles?: (target: DeleteTarget) => void
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState<readonly number[]>(() => firstOpen(series.seasons))

  function toggle(id: number) {
    setOpen((current) => (current.includes(id) ? current.filter((entry) => entry !== id) : [...current, id]))
  }

  return (
    <Section title={t('series.seasons.title')} intro={t('series.seasons.intro')}>
      <div className="flex flex-col gap-3">
        {ordered(series.seasons).map((season) => (
          <SeasonRow
            key={season.id}
            title={title}
            season={season}
            open={open.includes(season.id)}
            onToggle={() => toggle(season.id)}
            reloadToken={reloadToken}
            onSwitched={onSwitched}
            onSearch={onSearch}
            searchBlocked={searchBlocked}
            onFilesChanged={onFilesChanged}
            onDeleteFiles={onDeleteFiles}
          />
        ))}
      </div>
    </Section>
  )
}

function SeasonRow({
  title,
  season,
  open,
  onToggle,
  reloadToken,
  onSwitched,
  onSearch,
  searchBlocked,
  onFilesChanged,
  onDeleteFiles,
}: {
  title: TitleDetail
  season: SeasonBrief
  open: boolean
  onToggle: () => void
  reloadToken: number
  onSwitched: () => void
  onSearch?: (scope: SearchStartBody) => void
  searchBlocked: boolean
  onFilesChanged?: (detail: TitleDetail) => void
  onDeleteFiles?: (target: DeleteTarget) => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [data, setData] = useState<SeasonEpisodes | null>(null)
  const [loadProblem, setLoadProblem] = useState<unknown>(null)
  const [retries, setRetries] = useState(0)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const [episodeOpen, setEpisodeOpen] = useState<number | null>(null)
  const [allSpecials, setAllSpecials] = useState(false)
  const notify = useNotice()
  const versions = title.versions.filter((version) => definitionIdOf(version) !== null)
  const specials = season.number === 0
  const number = (value: number) => formatNumber(value, language)

  useEffect(() => {
    if (!open) return
    const abort = new AbortController()
    setLoadProblem(null)
    libraryApi.season(title.id, season.id, abort.signal).then(
      (result) => {
        if (!abort.signal.aborted) setData(result)
      },
      (error: unknown) => {
        if (!abort.signal.aborted) setLoadProblem(error)
      },
    )
    return () => abort.abort()
  }, [open, title.id, season.id, reloadToken, retries])

  async function release(versionId: number, fileId: number) {
    if (!onFilesChanged) return
    setBusy(true)
    setProblem(null)
    try {
      onFilesChanged(await libraryApi.releaseFile(title.id, versionId, fileId))
      notify(t('series.episode.released'))
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  async function run(request: () => Promise<SeasonEpisodes>) {
    setBusy(true)
    setProblem(null)
    try {
      setData(await request())
      onSwitched()
    } catch (error) {
      setProblem(error)
      // Inzwischen fuellt Sonarr die Fassung. Die Seite laedt nach, der Schalter verschwindet.
      if (error instanceof ApiError && error.code === 'version_fed_by_source') onSwitched()
    } finally {
      setBusy(false)
    }
  }

  const counts = versions.map((version) => {
    const brief = season.versions.find((entry) => entry.version_id === definitionIdOf(version))
    if (!brief) return null
    if (brief.watched_episodes === 0) return { id: version.id, text: t('series.seasons.unwatched', { label: version.label }) }
    const coming = Math.max(0, brief.watched_episodes - brief.aired_watched)
    const text = t('series.seasons.count', { label: version.label, have: number(brief.have), total: number(brief.aired_watched) })
    const parts = [text]
    if (coming > 0) parts.push(t('series.seasons.coming', { count: coming, value: number(coming) }))
    // Seit S4: wie viele Folgen dieser Staffel gerade ein Download dieser Fassung haelt (Entscheidung 42).
    if ((brief.loading ?? 0) > 0) parts.push(t('series.seasons.loading_count', { count: brief.loading ?? 0, value: number(brief.loading ?? 0) }))
    return { id: version.id, text: parts.join(' · ') }
  })

  const episodes = data?.episodes ?? []
  const columns = 3 + versions.length
  // Specials einer Fassung aus Sonarr (S4, Entscheidung 48): erst die, die Sonarr kennt, der Rest hinter einem Knopf.
  const splitSpecials = specials && episodes.some((episode) => episode.known_to_source === true || episode.known_to_source === false)
  const hiddenSpecials = splitSpecials && !allSpecials ? episodes.filter((episode) => episode.known_to_source !== true) : []
  const shownEpisodes = hiddenSpecials.length > 0 ? episodes.filter((episode) => episode.known_to_source === true) : episodes
  // Specials, die nur Sonarr kennt (Entscheidung 47): grau, ohne Suchen und Laden.
  const sourceOnly = specials
    ? versions.flatMap((version) => (version.source_only_episodes ?? []).filter((entry) => entry.season === 0).map((entry) => ({ version, entry })))
    : []
  const watchedSpecials = Math.max(0, ...season.versions.map((entry) => entry.watched_episodes))

  return (
    <div className="rounded-xl border border-ink-700 bg-ink-900/60">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-3">
        <button type="button" aria-expanded={open} onClick={onToggle} className="flex min-w-0 items-center gap-2 text-left font-semibold text-mist-100 hover:text-accent-400">
          <Symbol name="chevronDown" className={'h-4 w-4 shrink-0 transition-transform ' + (open ? '' : '-rotate-90')} />
          <span className="wrap-anywhere">{seasonName(t, season)}</span>
          <span className="text-xs font-normal text-mist-500 tabular-nums">
            {specials
              ? t('series.seasons.specialsSummary', { tmdb: number(season.episodes), watched: number(watchedSpecials) })
              : t('series.seasons.episodes', { count: season.episodes, value: number(season.episodes) })}
          </span>
        </button>
        <span className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-mist-400 tabular-nums">
          {counts.map((entry) => entry !== null && <span key={entry.id}>{entry.text}</span>)}
          {onSearch && (
            <Button
              size="sm"
              variant="ghost"
              disabled={searchBlocked}
              aria-label={t('search.series.seasonLabel', { season: seasonName(t, season) })}
              onClick={() => onSearch({ scope: 'season', season: season.number })}
            >
              <Symbol name="search" />
              {t('search.series.seasonAction')}
            </Button>
          )}
        </span>
      </div>

      {open && (
        <div className="flex flex-col gap-3 border-t border-ink-700 px-4 py-3">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <span className="text-sm text-mist-500">{t('series.seasons.whole')}</span>
            {versions.map((version) => {
              const brief = season.versions.find((entry) => entry.version_id === definitionIdOf(version))
              const fed = isFed(version)
              const versionId = definitionIdOf(version)
              if (!brief || versionId === null) return null
              return (
                <span key={version.id} className="flex flex-wrap items-center gap-2">
                  <InlineSwitch
                    text={fed ? t('series.seasons.fedSeason', { label: version.label, name: version.source_name ?? '' }) : t('series.seasons.watchSeason', { label: version.label })}
                    checked={brief.watched}
                    disabled={fed || busy}
                    onChange={(watched) => void run(() => libraryApi.switchSeason(title.id, season.id, { version_id: versionId, watched }))}
                  />
                  {!fed && onDeleteFiles && brief.have > 0 && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      aria-label={t('series.seasons.deleteFilesLabel', { season: seasonName(t, season), label: version.label })}
                      onClick={() => onDeleteFiles({ kind: 'season', versionId, label: version.label, season: season.number, seasonName: seasonName(t, season) })}
                    >
                      <Symbol name="trash" />
                      {t('series.seasons.deleteFiles')}
                    </Button>
                  )}
                </span>
              )
            })}
          </div>

          {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
          {loadProblem !== null && (
            <div className="flex flex-col items-start gap-2">
              <FormMessage>{errorText(t, loadProblem)}</FormMessage>
              <Button size="sm" variant="ghost" onClick={() => setRetries((count) => count + 1)}>
                <Symbol name="refresh" />
                {t('common.actions.retry')}
              </Button>
            </div>
          )}
          {data === null && loadProblem === null && (
            <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
              <Spinner />
              {t('series.seasons.loading')}
            </p>
          )}
          {data !== null && episodes.length === 0 && sourceOnly.length === 0 && <p className="text-sm text-mist-500">{t('series.seasons.empty')}</p>}
          {(episodes.length > 0 || sourceOnly.length > 0) && (
            // Die Tabelle scrollt am Telefon fuer sich, die Seite nicht. "relative" haelt auch die unsichtbaren Texte der Zellen darin,
            // sonst ragen sie als absolut gesetzte Elemente ueber den Rand der Seite.
            <div className="relative overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-mist-500">
                    <th scope="col" className="w-12 py-2 pr-3 font-medium">
                      {t('series.seasons.number')}
                    </th>
                    <th scope="col" className="py-2 pr-3 font-medium whitespace-nowrap">
                      {t('series.seasons.date')}
                    </th>
                    <th scope="col" className="min-w-48 py-2 pr-3 font-medium">
                      {t('series.seasons.episodeTitle')}
                    </th>
                    {versions.map((version) => (
                      <th key={version.id} scope="col" className="px-2 py-2 text-center font-medium whitespace-nowrap">
                        {version.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {shownEpisodes.map((episode) => {
                    const expanded = episodeOpen === episode.id
                    const name = episode.name.trim() !== '' ? episode.name : t('series.seasons.untitled', { number: episode.number })
                    const late = episode.versions.some((entry) => entry.late)
                    const corrected = ownerNumber(episode) !== null
                    return (
                      <Fragment key={episode.id}>
                        <tr
                          onClick={() => setEpisodeOpen(expanded ? null : episode.id)}
                          className={'cursor-pointer border-t border-ink-700/60 hover:bg-ink-800/60 ' + (expanded ? 'bg-ink-800/60' : '')}
                        >
                          <td className="py-2 pr-3 text-mist-500 tabular-nums">{episode.number}</td>
                          <td className="py-2 pr-3 whitespace-nowrap text-mist-400 tabular-nums">
                            {episode.air_date ? formatCalendarDate(episode.air_date, language) : t('series.seasons.noDate')}
                          </td>
                          <td className="py-2 pr-3">
                            <span className="flex flex-wrap items-center gap-2">
                              <button
                                type="button"
                                aria-expanded={expanded}
                                aria-label={t('series.seasons.openLabel', { number: episode.number, name })}
                                className={'text-left wrap-anywhere hover:text-accent-400 ' + (episode.tmdb_gone ? 'text-mist-500 line-through' : 'text-mist-100')}
                              >
                                {name}
                                {episode.name_en && <span className="block text-xs text-mist-500">{episode.name_en}</span>}
                              </button>
                              {episode.tmdb_gone && <Badge>{t('series.seasons.gone')}</Badge>}
                              {late && <Badge tone="bad">{t('series.seasons.lateTag')}</Badge>}
                              {corrected && <Badge tone="info">{t('series.seasons.correctedTag')}</Badge>}
                            </span>
                          </td>
                          {versions.map((version) => (
                            <td key={version.id} className="px-2 py-2 text-center">
                              <StateCell episode={episode} entry={episode.versions.find((entry) => entry.version_id === definitionIdOf(version)) ?? null} version={version} />
                            </td>
                          ))}
                        </tr>
                        {expanded && (
                          <tr className="border-t border-ink-700/60">
                            <td colSpan={columns} className="py-3">
                              <EpisodePanel
                                episode={episode}
                                versions={versions}
                                busy={busy}
                                onSwitch={(versionId, watched) => void run(() => libraryApi.switchEpisode(title.id, episode.id, { version_id: versionId, watched }))}
                                onSearch={onSearch ? () => onSearch({ scope: 'episode', episode_id: episode.id }) : undefined}
                                searchBlocked={searchBlocked}
                                correction={{ titleId: title.id, seasonEpisodes: episodes, anime: title.series?.type === 'anime', onSaved: () => setRetries((count) => count + 1) }}
                                onRelease={onFilesChanged ? (versionId, fileId) => void release(versionId, fileId) : undefined}
                                onDeleteFile={
                                  onDeleteFiles
                                    ? (version, fileId, fileName) => {
                                        const versionId = definitionIdOf(version)
                                        if (versionId !== null) onDeleteFiles({ kind: 'episode', versionId, label: version.label, fileId, fileName })
                                      }
                                    : undefined
                                }
                              />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    )
                  })}
                  {sourceOnly.map(({ version, entry }) => (
                    <tr key={`source-${version.id}-${entry.episode}`} className="border-t border-ink-700/60 text-mist-500">
                      <td className="py-2 pr-3 tabular-nums">{entry.episode}</td>
                      <td className="py-2 pr-3 whitespace-nowrap tabular-nums">{entry.air_date ? formatCalendarDate(entry.air_date, language) : t('series.seasons.noDate')}</td>
                      <td className="py-2 pr-3">
                        <span className="flex flex-col gap-0.5">
                          <span className="flex flex-wrap items-center gap-2">
                            <span className="wrap-anywhere">{entry.name.trim() !== '' ? entry.name : t('series.seasons.untitled', { number: entry.episode })}</span>
                            <Badge>{t('series.seasons.sourceOnlyTitle', { name: version.source_name ?? '' })}</Badge>
                          </span>
                          <span className="text-xs">{t('series.seasons.sourceOnlyText')}</span>
                        </span>
                      </td>
                      {versions.map((other) => (
                        <td key={other.id} className="px-2 py-2 text-center text-xs">
                          {other.id === version.id && entry.has_file ? t('series.seasons.hasFile') : null}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {hiddenSpecials.length > 0 && (
            <div>
              <Button size="sm" variant="ghost" onClick={() => setAllSpecials(true)}>
                <Symbol name="chevronDown" />
                {t('series.seasons.moreFromTmdb', { count: hiddenSpecials.length, value: number(hiddenSpecials.length) })}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

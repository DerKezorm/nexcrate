import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError, errorText } from '../../api/client'
import { diskApi } from '../../api/disk'
import { libraryApi } from '../../api/library'
import { cleanYear, tmdbApi } from '../../api/tmdb'
import type { DiskFolder, DiskRoot, TitleDetail, TitleVersion, TmdbResult, WatchRule } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { PosterImage } from '../../components/PosterImage'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Spinner, Toggle } from '../../components/ui'
import { formatList, formatNumber } from '../../lib/format'
import { WATCH_RULES } from '../library/seriesChoice'
import { SERIES_VERSIONS_TAB_PATH, TMDB_TAB_PATH } from '../settings/tabs'
import { definitionOf, isFromSource } from '../title/versionDefinitions'
import { useVersions } from '../versions/useVersions'
import { fromText, shownSeasons } from './diskText'
import { pickedFromProposal, pickedFromResult, type PickedMovie } from './pickedMovie'

/** Gesucht wird erst, wenn so lange nichts getippt wurde. */
export const SERIES_ASSIGN_SEARCH_DELAY_MS = 300

const SELECT_CLASS =
  'rounded-full border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60'

/**
 * Eine Fassung mit eigenem Serienordner oder aus einer Sonarr-Verbindung kann diesen Ordner nicht nehmen
 * (Entscheidungen 29 und 26).
 */
function takenBy(version: TitleVersion): 'folder' | 'source' | null {
  if (isFromSource(version)) return 'source'
  return typeof version.relative_path === 'string' && version.relative_path !== '' ? 'folder' : null
}

function Heading({ children }: { children: ReactNode }) {
  return <h3 className="text-sm font-semibold text-mist-100">{children}</h3>
}

/**
 * "{{name}} zuordnen" fuer einen Serienordner (S6, Entscheidung 27): die Serie aus den Vorschlaegen der Zeile oder aus
 * TMDBs Seriensuche, die Fassung (die der Wurzel zuerst), und was sie ueberwachen soll, wie beim Hinzufuegen einer
 * Serie. Danach liest nexcrate den Ordner ein und ordnet die Dateien den Folgen zu; auf der Platte aendert sich nur
 * die release.nex je Staffelordner.
 */
export function SeriesAssignDialog({
  folder,
  root,
  onClose,
  onAssigned,
}: {
  folder: DiskFolder
  /** Die Wurzel, in der der Ordner liegt. Ihre Fassungen stehen oben. null, wenn sie unbekannt ist. */
  root: DiskRoot | null
  onClose: () => void
  onAssigned: (detail: TitleDetail) => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const { versions, error: versionsError, reload: reloadVersions } = useVersions('series')
  const proposals = Array.isArray(folder.proposals) ? folder.proposals : []
  const rootVersions = root?.versions ?? []
  const seasons = shownSeasons(folder)
  const videos = folder.series?.videos ?? 0

  const [query, setQuery] = useState(folder.parsed?.title ?? '')
  const [year, setYear] = useState(folder.parsed?.year ? String(folder.parsed.year) : '')
  const [results, setResults] = useState<TmdbResult[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<unknown>(null)
  const searchRun = useRef(0)

  const [picked, setPicked] = useState<PickedMovie | null>(proposals.length > 0 ? pickedFromProposal(proposals[0]) : null)
  const [detail, setDetail] = useState<TitleDetail | null>(null)
  const [versionId, setVersionId] = useState<number | null>(rootVersions.length === 1 ? rootVersions[0].id : null)
  const [rule, setRule] = useState<WatchRule>('all')
  const [fromSeason, setFromSeason] = useState(1)
  const [busy, setBusy] = useState(false)
  const [keep, setKeep] = useState(true)
  const [problem, setProblem] = useState<unknown>(null)

  const q = query.trim()
  const searchYear = cleanYear(year)

  // TMDBs Suche nach Serien, wie in "Serie hinzufuegen": getippt wird sofort, gesucht nach einer Pause.
  useEffect(() => {
    const run = ++searchRun.current
    if (q === '') {
      setResults(null)
      setSearching(false)
      setSearchError(null)
      return
    }
    const abort = new AbortController()
    const timer = window.setTimeout(() => {
      setSearching(true)
      setSearchError(null)
      tmdbApi.search({ q, year: searchYear, kind: 'series' }, abort.signal).then(
        (found) => {
          if (run !== searchRun.current) return
          setResults(found.results)
          setSearching(false)
        },
        (error: unknown) => {
          if (run !== searchRun.current || abort.signal.aborted) return
          setSearching(false)
          setResults([])
          setSearchError(error)
        },
      )
    }, SERIES_ASSIGN_SEARCH_DELAY_MS)
    return () => {
      window.clearTimeout(timer)
      abort.abort()
    }
  }, [q, searchYear])

  // Eine Serie, die schon in der Bibliothek steht: Ihre Fassungen sagen, welche einen Serienordner hat.
  const pickedTitleId = picked?.title_id ?? null
  useEffect(() => {
    setDetail(null)
    if (pickedTitleId === null) return
    let current = true
    libraryApi.detail(String(pickedTitleId)).then(
      (found) => current && setDetail(found),
      () => undefined,
    )
    return () => {
      current = false
    }
  }, [pickedTitleId])

  const definitions = versions ?? []
  const taken = new Map<number, 'folder' | 'source'>()
  for (const version of detail?.versions ?? []) {
    const id = definitionOf(version, definitions)
    const reason = takenBy(version)
    if (id !== null && reason !== null) taken.set(id, reason)
  }
  // Die Fassungen der Wurzel zuerst, danach die uebrigen Serienfassungen.
  const rootIds = rootVersions.map((version) => version.id)
  const ordered = [...definitions].sort((left, right) => Number(rootIds.includes(right.id)) - Number(rootIds.includes(left.id)))
  const fixed = rootVersions.length === 1 && !taken.has(rootVersions[0].id) ? rootVersions[0] : null
  const chosenVersion = versionId !== null && !taken.has(versionId) ? versionId : null
  const canSubmit = picked !== null && chosenVersion !== null && !busy

  // Der Ordner, den die abgelehnte Fassung schon hat (409 `version_has_files`).
  const takenLocation = problem instanceof ApiError && problem.code === 'version_has_files' && typeof problem.values.location === 'string' ? problem.values.location : null
  const noTmdb = searchError instanceof ApiError && (searchError.code === 'tmdb_not_configured' || searchError.code === 'tmdb_token_rejected' || searchError.code === 'tmdb_token_unreadable')
  const proposalIds = new Set(proposals.map((proposal) => proposal.tmdb_id))
  const tmdbResults = (results ?? []).filter((result) => !proposalIds.has(result.tmdb_id))

  const ruleLabel = (value: WatchRule): string =>
    ({
      all: t('series.rule.all'),
      future: t('series.rule.future'),
      missing: t('series.rule.missing'),
      from_season: t('series.rule.from_season'),
      none: t('series.rule.none'),
    })[value]

  function choose(next: PickedMovie) {
    setPicked((current) => (current?.tmdb_id === next.tmdb_id ? current : next))
    setProblem(null)
  }

  function close() {
    if (!busy) onClose()
  }

  async function submit() {
    if (!canSubmit || picked === null || chosenVersion === null) return
    setBusy(true)
    setProblem(null)
    try {
      onAssigned(
        await diskApi.assignSeries(folder.id, {
          tmdb_id: picked.tmdb_id,
          version_id: chosenVersion,
          rule,
          from_season: rule === 'from_season' ? fromSeason : null,
          keep_as_is: keep,
        }),
      )
    } catch (error) {
      setBusy(false)
      setProblem(error)
      // Die Liste der Fassungen ist veraltet, etwa weil eine inzwischen umgebaut wurde.
      if (error instanceof ApiError && error.code === 'version_kind_mismatch') reloadVersions()
    }
  }

  const seriesButton = (candidate: PickedMovie, meta: string | null, key: string) => {
    const selected = picked?.tmdb_id === candidate.tmdb_id
    return (
      <li key={key}>
        <button
          type="button"
          aria-pressed={selected}
          onClick={() => choose(candidate)}
          disabled={busy}
          className={
            'flex w-full items-center gap-3 rounded-xl border p-2.5 text-left transition-colors ' +
            (selected ? 'border-accent-500/60 bg-accent-500/10' : 'border-ink-700 bg-ink-900/60 hover:border-accent-500/50 hover:bg-ink-800')
          }
        >
          <PosterImage url={candidate.poster_url} className="w-9 shrink-0" />
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-semibold wrap-anywhere text-mist-100">{candidate.year ? `${candidate.title} (${candidate.year})` : candidate.title}</span>
              {selected && <Badge tone="accent">{t('disk.assign.chosen')}</Badge>}
              {candidate.title_id !== null && (
                <Badge tone="ok">
                  <Symbol name="check" className="h-3.5 w-3.5" />
                  {t('library.add.inLibrary')}
                </Badge>
              )}
            </span>
            {meta && <span className="text-xs text-mist-500">{meta}</span>}
          </span>
          {selected && <Symbol name="check" className="h-4 w-4 shrink-0 text-accent-400" />}
        </button>
      </li>
    )
  }

  return (
    <Dialog
      open
      wide
      title={t('disk.assign.title', { name: folder.relative_path })}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void submit()} loading={busy} disabled={!canSubmit}>
            {t('disk.assign.submit')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-6">
        <section className="flex flex-col gap-1 text-xs text-mist-500" aria-label={t('disk.series.assign.folder')}>
          <Heading>{t('disk.series.assign.folder')}</Heading>
          <p className="wrap-anywhere text-sm text-mist-200">{folder.relative_path}</p>
          {videos > 0 && <p className="tabular-nums">{t('disk.series.row.videos', { count: videos, value: formatNumber(videos, language) })}</p>}
          {seasons.names.length > 0 && (
            <p className="wrap-anywhere">
              {t('disk.series.row.seasons', { names: formatList(seasons.names, language) })}
              {seasons.more > 0 && ` ${t('disk.series.row.moreSeasons', { count: seasons.more })}`}
            </p>
          )}
        </section>

        <section className="flex flex-col gap-3" aria-label={t('disk.series.assign.series')}>
          <Heading>{t('disk.series.assign.series')}</Heading>
          <div className="grid grid-cols-[minmax(0,1fr)_5.5rem] gap-2 sm:grid-cols-[minmax(0,1fr)_7rem] sm:gap-3">
            <div className="relative min-w-0">
              <Symbol name="search" className="pointer-events-none absolute top-1/2 left-3.5 h-4 w-4 -translate-y-1/2 text-mist-600" />
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t('disk.series.assign.search')}
                aria-label={t('disk.series.assign.search')}
                maxLength={200}
                className="w-full rounded-full border border-ink-700 bg-ink-900 py-2.5 pr-4 pl-10 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
              />
            </div>
            <input
              type="text"
              inputMode="numeric"
              maxLength={4}
              value={year}
              onChange={(event) => setYear(event.target.value.replace(/\D/g, ''))}
              placeholder={t('disk.assign.year')}
              aria-label={t('disk.assign.year')}
              className="w-full min-w-0 rounded-full border border-ink-700 bg-ink-900 px-4 py-2.5 text-sm text-mist-100 tabular-nums placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
            />
          </div>
          {proposals.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <h4 className="text-xs font-semibold text-mist-400">{t('disk.assign.proposals')}</h4>
              <ul aria-label={t('disk.assign.proposals')} className="flex flex-col gap-1.5">
                {proposals.map((proposal, index) => seriesButton(pickedFromProposal(proposal), fromText(t, proposal.from), `proposal-${proposal.tmdb_id}-${index}`))}
              </ul>
            </div>
          )}
          {picked !== null && picked.from === null && !proposalIds.has(picked.tmdb_id) && !tmdbResults.some((result) => result.tmdb_id === picked.tmdb_id) && (
            <ul aria-label={t('disk.assign.chosen')} className="flex flex-col gap-1.5">
              {seriesButton(picked, null, `picked-${picked.tmdb_id}`)}
            </ul>
          )}
          <div className="flex flex-col gap-1.5">
            <h4 className="text-xs font-semibold text-mist-400">{t('disk.assign.results')}</h4>
            {noTmdb ? (
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-mist-300">
                <span>{t('disk.noTmdb')}</span>
                <Link to={TMDB_TAB_PATH} onClick={close} className="font-medium text-accent-400 hover:underline">
                  {t('disk.noTmdbLink')}
                </Link>
              </p>
            ) : searchError !== null ? (
              <FormMessage>{errorText(t, searchError)}</FormMessage>
            ) : q === '' ? (
              <p className="text-sm text-mist-500">{t('disk.series.assign.idle')}</p>
            ) : results === null || (searching && tmdbResults.length === 0) ? (
              <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
                <Spinner />
                {t('disk.assign.searching')}
              </p>
            ) : tmdbResults.length === 0 ? (
              <p className="text-sm text-mist-500">{t('disk.assign.noResults')}</p>
            ) : (
              <ul aria-label={t('disk.assign.results')} className={'flex flex-col gap-1.5 ' + (searching ? 'opacity-60' : '')} aria-busy={searching}>
                {tmdbResults.map((result) =>
                  seriesButton(
                    pickedFromResult(result),
                    result.original_title && result.original_title !== result.title ? t('library.add.originalTitle', { title: result.original_title }) : null,
                    `result-${result.tmdb_id}`,
                  ),
                )}
              </ul>
            )}
          </div>
        </section>

        <section className="flex flex-col gap-2" aria-label={t('disk.series.assign.version')}>
          <Heading>{t('disk.series.assign.version')}</Heading>
          {versionsError !== null && <FormMessage>{errorText(t, versionsError)}</FormMessage>}
          {versions === null ? (
            versionsError === null && (
              <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
                <Spinner />
                {t('common.loading')}
              </p>
            )
          ) : versions.length === 0 ? (
            <div className="flex flex-col items-start gap-2 rounded-xl border border-dashed border-ink-600 p-4">
              <p className="text-sm text-mist-300">{t('disk.series.assign.noVersions')}</p>
              <Link to={SERIES_VERSIONS_TAB_PATH} onClick={close} className="inline-flex items-center gap-2 text-sm font-semibold text-accent-400 hover:underline">
                {t('disk.assign.noVersionsAction')}
                <Symbol name="arrow" />
              </Link>
            </div>
          ) : fixed !== null ? (
            <div className="flex flex-col gap-0.5">
              <p className="text-sm font-medium text-mist-100">{t('disk.assign.versionFixed', { label: fixed.label })}</p>
              <p className="text-xs text-mist-500">{t('disk.series.assign.versionFixedHint')}</p>
            </div>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {ordered.map((version) => {
                const reason = taken.get(version.id) ?? null
                const checked = chosenVersion === version.id
                return (
                  <li key={version.id}>
                    <label
                      className={
                        'flex items-center gap-3 rounded-xl border p-2.5 ' +
                        (reason !== null ? 'cursor-default border-ink-700 bg-ink-900/40 opacity-70' : checked ? 'cursor-pointer border-accent-500/50 bg-accent-500/5' : 'cursor-pointer border-ink-700 bg-ink-900/60')
                      }
                    >
                      <input
                        type="radio"
                        name="assign-series-version"
                        className="h-4 w-4 shrink-0 accent-accent-500"
                        checked={checked}
                        disabled={reason !== null || busy}
                        onChange={() => setVersionId(version.id)}
                      />
                      <span className="flex min-w-0 flex-1 flex-wrap items-center gap-2 text-sm font-semibold text-mist-100">
                        <span className="wrap-anywhere">{version.label}</span>
                        {reason === 'folder' && <Badge tone="neutral">{t('disk.series.assign.hasFolder')}</Badge>}
                        {reason === 'source' && <Badge tone="neutral">{t('disk.series.assign.fromSonarr')}</Badge>}
                      </span>
                    </label>
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        <section className="flex flex-col gap-2" aria-label={t('disk.series.assign.rule')}>
          <Heading>{t('disk.series.assign.rule')}</Heading>
          <div className="flex flex-wrap items-center gap-2">
            <select value={rule} disabled={busy} aria-label={t('disk.series.assign.rule')} onChange={(event) => setRule(event.target.value as WatchRule)} className={SELECT_CLASS}>
              {WATCH_RULES.map((value) => (
                <option key={value} value={value}>
                  {ruleLabel(value)}
                </option>
              ))}
            </select>
            {rule === 'from_season' && (
              <input
                type="text"
                inputMode="numeric"
                maxLength={4}
                value={String(fromSeason)}
                aria-label={t('disk.series.assign.fromSeason')}
                disabled={busy}
                onChange={(event) => setFromSeason(Math.max(1, Number(event.target.value.replace(/\D/g, '')) || 1))}
                className="w-20 rounded-full border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm text-mist-100 tabular-nums focus:border-accent-500 focus:outline-none"
              />
            )}
          </div>
        </section>

        <p className="flex items-start gap-2 text-sm text-mist-400">
          <Symbol name="info" className="mt-0.5 h-4 w-4 shrink-0 text-info-500" />
          <span>{t('disk.series.assign.reading')}</span>
        </p>
        {!canSubmit && !busy && (picked === null || chosenVersion === null) && versions !== null && versions.length > 0 && <p className="text-xs text-mist-500">{t('disk.series.assign.missingChoice')}</p>}
        <Toggle label={t('disk.keep.label')} hint={t('disk.keep.hint')} checked={keep} onChange={setKeep} disabled={busy} />
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        {/* Entscheidung 29: Die Fassung hat schon einen Serienordner. Welcher es ist, sagt der Server im Fehler. */}
        {takenLocation !== null && <p className="text-sm wrap-anywhere text-mist-300">{t('disk.series.assign.folderOf', { location: takenLocation })}</p>}
      </div>
    </Dialog>
  )
}

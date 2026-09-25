import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError, errorText } from '../../api/client'
import { foreignApi } from '../../api/foreign'
import { LIBRARY_PAGE_SIZE, libraryApi } from '../../api/library'
import { cleanYear, tmdbApi } from '../../api/tmdb'
import type { ForeignJob, MediaKind, TitleDetail, TitleSummary, TmdbResult } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { PosterImage } from '../../components/PosterImage'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Spinner } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { sizeText } from '../../lib/size'
import { percentOf } from '../../lib/states'
import { ASSIGN_SEARCH_DELAY_MS } from '../disk/AssignDialog'
import { fromText } from '../disk/diskText'
import { pickedFromProposal, pickedFromResult, type PickedMovie } from '../disk/pickedMovie'
import { TMDB_TAB_PATH, VERSIONS_TAB_PATH } from '../settings/tabs'
import { definitionOf, isFromSource } from '../title/versionDefinitions'
import { useVersions } from '../versions/useVersions'

/**
 * Auftraege in der Kategorie von nexcrate, die kein Download verfolgt (Befund 2 vom 22.09.2026), wie Radarrs "Manual
 * import required": der Name, was das Programm dazu sagt, ein Vorschlag aus dem Namen, "Zuordnen und importieren" und
 * "Entfernen". nexcrate legt sie nie von selbst ab.
 */
export function ForeignJobs({ jobs, onChanged }: { jobs: ForeignJob[]; onChanged: (message?: string) => void }) {
  const { t, i18n } = useTranslation()
  if (jobs.length === 0) return null
  const title = t('downloads.foreign.title')
  return (
    <section aria-label={title} className="flex flex-col gap-4">
      <div>
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <Symbol name="alert" className="h-5 w-5 text-bad-500" />
          {title}
          <Badge tone="bad">{formatNumber(jobs.length, i18n.language)}</Badge>
        </h2>
        <p className="mt-1 max-w-3xl text-sm text-mist-500">{t('downloads.foreign.intro')}</p>
      </div>
      <ul className="flex flex-col gap-4">
        {jobs.map((job) => (
          <li key={job.id} className="min-w-0">
            <ForeignCard job={job} onChanged={onChanged} />
          </li>
        ))}
      </ul>
    </section>
  )
}

function stateText(t: ReturnType<typeof useTranslation>['t'], job: ForeignJob, language: string): string {
  const client = job.client?.name ?? t('downloads.foreign.unknownClient')
  switch (job.state) {
    case 'completed':
      return t('downloads.foreign.completed', { client })
    case 'failed':
    case 'problem':
      return t('downloads.foreign.failed', { client })
    case 'downloading':
    case 'paused': {
      const percent = job.progress !== null ? `${formatNumber(Math.round(percentOf(job.progress)), language)} %` : '…'
      return t('downloads.foreign.running', { client, percent })
    }
    default:
      return t('downloads.foreign.queued', { client })
  }
}

function ForeignCard({ job, onChanged }: { job: ForeignJob; onChanged: (message?: string) => void }) {
  const { t, i18n } = useTranslation()
  const [dialog, setDialog] = useState<'assign' | 'remove' | null>(null)
  const proposal = job.proposals[0] ?? null
  const failed = job.state === 'failed' || job.state === 'problem'
  const size = job.size_bytes !== null ? sizeText(t, job.size_bytes, i18n.language) : null
  return (
    <article className="flex min-w-0 flex-col gap-3 rounded-2xl border border-bad-500/40 bg-ink-850 p-4">
      <p className="font-mono text-sm leading-5 wrap-anywhere text-mist-100">{job.name}</p>
      <p className="text-sm text-mist-300">
        {stateText(t, job, i18n.language)}
        {size !== null && <span className="text-mist-500 tabular-nums"> · {size}</span>}
      </p>
      {proposal !== null && (
        <p className="text-sm text-mist-200">
          {t('downloads.foreign.proposal', {
            title: proposal.year ? `${proposal.title} (${proposal.year})` : proposal.title,
          })}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {!failed && (
          <Button size="sm" onClick={() => setDialog('assign')} aria-label={t('downloads.foreign.assignLabel', { name: job.name })}>
            <Symbol name="check" />
            {t('downloads.foreign.assign')}
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={() => setDialog('remove')} aria-label={t('downloads.foreign.removeLabel', { name: job.name })}>
          <Symbol name="trash" />
          {t('downloads.foreign.remove')}
        </Button>
      </div>
      {dialog === 'assign' && (
        <ForeignAssignDialog
          job={job}
          onClose={() => setDialog(null)}
          onDone={(message) => {
            setDialog(null)
            onChanged(message)
          }}
        />
      )}
      {dialog === 'remove' && (
        <ForeignRemoveDialog
          job={job}
          onClose={() => setDialog(null)}
          onDone={() => {
            setDialog(null)
            onChanged(t('downloads.foreign.removed'))
          }}
        />
      )}
    </article>
  )
}

function ForeignRemoveDialog({ job, onClose, onDone }: { job: ForeignJob; onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function remove() {
    setBusy(true)
    setProblem(null)
    try {
      await foreignApi.remove(job.id)
      onDone()
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  function close() {
    if (!busy) onClose()
  }

  return (
    <Dialog
      open
      title={t('downloads.foreign.removeTitle', { name: job.name })}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="danger" onClick={() => void remove()} loading={busy}>
            {t('downloads.foreign.removeConfirm')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-mist-300">
          {t('downloads.foreign.removeText', {
            client: job.client?.name ?? t('downloads.foreign.unknownClient'),
          })}
        </p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

function Heading({ children }: { children: ReactNode }) {
  return <h3 className="text-sm font-semibold text-mist-100">{children}</h3>
}

/**
 * "Zuordnen und importieren": wie "Zuordnen" auf der Seite Ordner, der Film aus den Vorschlaegen oder TMDBs Suche und
 * die Fassung. Eine Fassung aus Radarr nimmt nichts; eine mit Datei bekommt die neue, die alte geht in den Papierkorb.
 */
function ForeignAssignDialog({ job, onClose, onDone }: { job: ForeignJob; onClose: () => void; onDone: (message: string) => void }) {
  const { t } = useTranslation()
  const [kind, setKind] = useState<MediaKind>(job.kind === 'series' ? 'series' : 'movie')
  const [libraryPick, setLibraryPick] = useState<{
    title: TitleSummary
    versionId: number
  } | null>(null)
  const { versions, error: versionsError } = useVersions('movie')
  const [query, setQuery] = useState(job.parsed.title ?? '')
  const [year, setYear] = useState(job.parsed.year ? String(job.parsed.year) : '')
  const [results, setResults] = useState<TmdbResult[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<unknown>(null)
  const searchRun = useRef(0)
  const [picked, setPicked] = useState<PickedMovie | null>(job.proposals.length > 0 ? pickedFromProposal(job.proposals[0]) : null)
  const [detail, setDetail] = useState<TitleDetail | null>(null)
  const [versionId, setVersionId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const q = query.trim()
  const searchYear = cleanYear(year)

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
      tmdbApi.search({ q, year: searchYear }, abort.signal).then(
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
    }, ASSIGN_SEARCH_DELAY_MS)
    return () => {
      window.clearTimeout(timer)
      abort.abort()
    }
  }, [q, searchYear])

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
  const fed = new Set<number>()
  const withFile = new Set<number>()
  for (const version of detail?.versions ?? []) {
    const id = definitionOf(version, definitions)
    if (id === null) continue
    if (isFromSource(version)) fed.add(id)
    else if (version.state === 'available' || version.state === 'upgrade') withFile.add(id)
  }
  const chosenVersion = versionId !== null && !fed.has(versionId) ? versionId : definitions.length === 1 && !fed.has(definitions[0].id) ? definitions[0].id : null
  const canSubmit = !busy && (kind === 'movie' ? picked !== null && chosenVersion !== null : libraryPick !== null)
  const noTmdb = searchError instanceof ApiError && (searchError.code === 'tmdb_not_configured' || searchError.code === 'tmdb_token_rejected' || searchError.code === 'tmdb_token_unreadable')
  const proposalIds = new Set(job.proposals.map((proposal) => proposal.tmdb_id))
  const tmdbResults = (results ?? []).filter((result) => !proposalIds.has(result.tmdb_id))

  function close() {
    if (!busy) onClose()
  }

  async function submit() {
    if (!canSubmit) return
    const body =
      kind !== 'movie'
        ? libraryPick !== null
          ? {
              title_id: libraryPick.title.id,
              version_id: libraryPick.versionId,
            }
          : null
        : picked !== null && chosenVersion !== null
          ? picked.title_id !== null
            ? { title_id: picked.title_id, version_id: chosenVersion }
            : { tmdb_id: picked.tmdb_id, version_id: chosenVersion }
          : null
    const shown = kind !== 'movie' ? libraryPick?.title : picked
    if (body === null || !shown) return
    setBusy(true)
    setProblem(null)
    try {
      await foreignApi.adopt(job.id, body)
      onDone(
        t('downloads.foreign.imported', {
          name: job.name,
          title: shown.year ? `${shown.title} (${shown.year})` : shown.title,
        }),
      )
    } catch (error) {
      setBusy(false)
      setProblem(error)
    }
  }

  const movieButton = (candidate: PickedMovie, meta: string | null, key: string) => {
    const selected = picked?.tmdb_id === candidate.tmdb_id
    return (
      <li key={key}>
        <button
          type="button"
          aria-pressed={selected}
          onClick={() => setPicked(candidate)}
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
      title={t('downloads.foreign.dialogTitle', { name: job.name })}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void submit()} loading={busy} disabled={!canSubmit}>
            {t('downloads.foreign.assign')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-6">
        <p className="text-sm text-mist-400">{t('downloads.foreign.dialogIntro')}</p>
        <div role="radiogroup" aria-label={t('downloads.foreign.kind')} className="flex flex-wrap gap-2">
          {(['movie', 'series', 'album'] as const).map((choice) => (
            <button
              key={choice}
              type="button"
              role="radio"
              aria-checked={kind === choice}
              disabled={busy}
              onClick={() => {
                setKind(choice)
                setLibraryPick(null)
                setProblem(null)
              }}
              className={
                'rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors ' +
                (kind === choice ? 'border-accent-500/60 bg-accent-500/10 text-accent-300' : 'border-ink-700 bg-ink-900/60 text-mist-300 hover:border-accent-500/50')
              }
            >
              {kindText(t, choice)}
            </button>
          ))}
        </div>
        {kind !== 'movie' && <LibraryChoice key={kind} kind={kind} job={job} busy={busy} onChange={setLibraryPick} />}
        {kind === 'movie' && (
          <>
            <section className="flex flex-col gap-3" aria-label={t('disk.assign.movie')}>
              <Heading>{t('disk.assign.movie')}</Heading>
              <div className="grid grid-cols-[minmax(0,1fr)_5.5rem] gap-2 sm:grid-cols-[minmax(0,1fr)_7rem] sm:gap-3">
                <div className="relative min-w-0">
                  <Symbol name="search" className="pointer-events-none absolute top-1/2 left-3.5 h-4 w-4 -translate-y-1/2 text-mist-600" />
                  <input
                    type="search"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder={t('disk.assign.search')}
                    aria-label={t('disk.assign.search')}
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
              {job.proposals.length > 0 && (
                <div className="flex flex-col gap-1.5">
                  <h4 className="text-xs font-semibold text-mist-400">{t('disk.assign.proposals')}</h4>
                  <ul aria-label={t('disk.assign.proposals')} className="flex flex-col gap-1.5">
                    {job.proposals.map((proposal, index) => movieButton(pickedFromProposal(proposal), fromText(t, proposal.from), `proposal-${proposal.tmdb_id}-${index}`))}
                  </ul>
                </div>
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
                  <p className="text-sm text-mist-500">{t('disk.assign.idle')}</p>
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
                      movieButton(
                        pickedFromResult(result),
                        result.original_title && result.original_title !== result.title
                          ? t('library.add.originalTitle', {
                              title: result.original_title,
                            })
                          : null,
                        `result-${result.tmdb_id}`,
                      ),
                    )}
                  </ul>
                )}
              </div>
            </section>

            <section className="flex flex-col gap-2" aria-label={t('disk.assign.version')}>
              <Heading>{t('disk.assign.version')}</Heading>
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
                  <p className="text-sm text-mist-300">{t('disk.assign.noVersions')}</p>
                  <Link to={VERSIONS_TAB_PATH} onClick={close} className="inline-flex items-center gap-2 text-sm font-semibold text-accent-400 hover:underline">
                    {t('disk.assign.noVersionsAction')}
                    <Symbol name="arrow" />
                  </Link>
                </div>
              ) : (
                <ul className="flex flex-col gap-1.5">
                  {versions.map((version) => {
                    const source = fed.has(version.id)
                    const checked = chosenVersion === version.id
                    return (
                      <li key={version.id}>
                        <label
                          className={
                            'flex items-center gap-3 rounded-xl border p-2.5 ' +
                            (source
                              ? 'cursor-default border-ink-700 bg-ink-900/40 opacity-70'
                              : checked
                                ? 'cursor-pointer border-accent-500/50 bg-accent-500/5'
                                : 'cursor-pointer border-ink-700 bg-ink-900/60')
                          }
                        >
                          <input
                            type="radio"
                            name="foreign-version"
                            aria-label={version.label}
                            className="h-4 w-4 shrink-0 accent-accent-500"
                            checked={checked}
                            disabled={source || busy}
                            onChange={() => setVersionId(version.id)}
                          />
                          <span className="flex min-w-0 flex-1 flex-wrap items-center gap-2 text-sm font-semibold text-mist-100">
                            <span className="wrap-anywhere">{version.label}</span>
                            {withFile.has(version.id) && <Badge tone="neutral">{t('disk.assign.hasFile')}</Badge>}
                            {source && <Badge tone="neutral">{t('disk.assign.fromSource')}</Badge>}
                          </span>
                        </label>
                      </li>
                    )
                  })}
                </ul>
              )}
            </section>
          </>
        )}
        {!canSubmit && !busy && versions !== null && versions.length > 0 && <p className="text-xs text-mist-500">{t('disk.assign.missingChoice')}</p>}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

/**
 * Serie oder Album aus der Bibliothek, mit Suche: die Vorschlaege aus dem Namen zuerst. Die Fassung kommt aus den
 * Fassungen des Titels; ein Album hat genau eine.
 */
function LibraryChoice({ kind, job, busy, onChange }: { kind: 'series' | 'album'; job: ForeignJob; busy: boolean; onChange: (pick: { title: TitleSummary; versionId: number } | null) => void }) {
  const { t } = useTranslation()
  const [query, setQuery] = useState(kind === 'series' ? (job.parsed.title ?? '') : '')
  const [results, setResults] = useState<TitleSummary[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [title, setTitle] = useState<TitleSummary | null>(null)
  const [versionId, setVersionId] = useState<number | null>(null)
  const q = query.trim()

  useEffect(() => {
    const abort = new AbortController()
    const timer = window.setTimeout(() => {
      setError(null)
      libraryApi.list({ kind, state: null, q, sort: 'title', page: 1 }, abort.signal).then(
        (page) => setResults(page.items.slice(0, LIBRARY_PAGE_SIZE)),
        (problem: unknown) => {
          if (!abort.signal.aborted) {
            setResults([])
            setError(problem)
          }
        },
      )
    }, ASSIGN_SEARCH_DELAY_MS)
    return () => {
      window.clearTimeout(timer)
      abort.abort()
    }
  }, [kind, q])

  // Ein Vorschlag aus dem Namen steht vorn und ist gewaehlt.
  const proposed = useMemo(() => new Set(kind === 'series' ? (job.series_proposals ?? []).map((item) => item.title_id) : []), [kind, job.series_proposals])
  const listed = useMemo(() => [...(results ?? [])].sort((a, b) => Number(proposed.has(b.id)) - Number(proposed.has(a.id))), [results, proposed])
  useEffect(() => {
    if (title === null && listed.length > 0 && proposed.has(listed[0].id)) setTitle(listed[0])
  }, [title, listed, proposed])

  const versions = title?.versions ?? []
  const chosen = versionId !== null && versions.some((version) => version.id === versionId) ? versionId : versions.length === 1 ? versions[0].id : null
  useEffect(() => {
    onChange(title !== null && chosen !== null ? { title, versionId: chosen } : null)
  }, [title, chosen, onChange])

  const heading = kind === 'series' ? t('downloads.foreign.whichSeries') : t('downloads.foreign.whichAlbum')
  return (
    <>
      <section className="flex flex-col gap-3" aria-label={heading}>
        <Heading>{heading}</Heading>
        <div className="relative min-w-0">
          <Symbol name="search" className="pointer-events-none absolute top-1/2 left-3.5 h-4 w-4 -translate-y-1/2 text-mist-600" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('downloads.foreign.searchLibrary')}
            aria-label={t('downloads.foreign.searchLibrary')}
            maxLength={200}
            className="w-full rounded-full border border-ink-700 bg-ink-900 py-2.5 pr-4 pl-10 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
          />
        </div>
        {error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
        {results === null ? (
          <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
            <Spinner />
            {t('common.loading')}
          </p>
        ) : listed.length === 0 ? (
          <p className="text-sm text-mist-500">{t('downloads.foreign.nothingInLibrary')}</p>
        ) : (
          <ul aria-label={heading} className="flex flex-col gap-1.5">
            {listed.slice(0, 8).map((item) => {
              const selected = title?.id === item.id
              const label = [item.artist, item.year ? `${item.title} (${item.year})` : item.title].filter(Boolean).join(': ')
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    aria-pressed={selected}
                    disabled={busy}
                    onClick={() => {
                      setTitle(item)
                      setVersionId(null)
                    }}
                    className={
                      'flex w-full items-center gap-3 rounded-xl border p-2.5 text-left transition-colors ' +
                      (selected ? 'border-accent-500/60 bg-accent-500/10' : 'border-ink-700 bg-ink-900/60 hover:border-accent-500/50 hover:bg-ink-800')
                    }
                  >
                    <PosterImage url={item.poster_url} className="w-9 shrink-0" />
                    <span className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                      <span className="font-semibold wrap-anywhere text-mist-100">{label}</span>
                      {proposed.has(item.id) && <Badge tone="accent">{t('downloads.foreign.proposed')}</Badge>}
                    </span>
                    {selected && <Symbol name="check" className="h-4 w-4 shrink-0 text-accent-400" />}
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </section>
      {title !== null && (
        <section className="flex flex-col gap-2" aria-label={t('disk.assign.version')}>
          <Heading>{t('disk.assign.version')}</Heading>
          {versions.length === 0 ? (
            <p className="text-sm text-mist-300">{t('downloads.foreign.noVersion')}</p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {versions.map((version) => (
                <li key={version.id}>
                  <label
                    className={'flex cursor-pointer items-center gap-3 rounded-xl border p-2.5 ' + (chosen === version.id ? 'border-accent-500/50 bg-accent-500/5' : 'border-ink-700 bg-ink-900/60')}
                  >
                    <input
                      type="radio"
                      name="foreign-library-version"
                      aria-label={version.label}
                      className="h-4 w-4 shrink-0 accent-accent-500"
                      checked={chosen === version.id}
                      disabled={busy}
                      onChange={() => setVersionId(version.id)}
                    />
                    <span className="text-sm font-semibold wrap-anywhere text-mist-100">{version.label}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          {kind === 'series' && <p className="text-xs text-mist-500">{t('downloads.foreign.seriesHint')}</p>}
        </section>
      )}
    </>
  )
}

function kindText(t: ReturnType<typeof useTranslation>['t'], kind: MediaKind): string {
  switch (kind) {
    case 'series':
      return t('downloads.foreign.kinds.series')
    case 'album':
      return t('downloads.foreign.kinds.album')
    default:
      return t('downloads.foreign.kinds.movie')
  }
}

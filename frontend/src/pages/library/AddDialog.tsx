import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'

import { ApiError, errorText } from '../../api/client'
import { libraryApi } from '../../api/library'
import { tagsApi } from '../../api/tags'
import { cleanYear, tmdbApi } from '../../api/tmdb'
import type { OnDiskFolder, SeriesAdd, TmdbResult, TmdbSearch, TmdbState } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { PosterImage } from '../../components/PosterImage'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatNumber } from '../../lib/format'
import { AssignFromDisk } from '../disk/AssignFromDisk'
import { SERIES_VERSIONS_TAB_PATH, VERSIONS_TAB_PATH } from '../settings/tabs'
import { TmdbTokenForm } from '../settings/TmdbTokenForm'
import { useVersions } from '../versions/useVersions'
import { readAddChoice, storeAddChoice } from './addChoice'
import { SeriesChoices, SeriesFacts } from './SeriesChoices'
import { readSeriesChoice, storeSeriesChoice } from './seriesChoice'
import { choiceOf, useSeriesPreview, type SeriesPick } from './seriesPreview'

/** Gesucht wird erst, wenn so lange nichts getippt wurde. */
export const ADD_SEARCH_DELAY_MS = 300

type TokenTrouble = 'missing' | 'rejected' | null

/** Kein Token oder ein abgelehnter: Dann zeigt der Dialog statt Treffern die Anleitung und das Formular. */
function tokenTrouble(error: unknown): TokenTrouble {
  if (!(error instanceof ApiError)) return null
  if (error.code === 'tmdb_not_configured') return 'missing'
  if (error.code === 'tmdb_token_rejected') return 'rejected'
  return null
}

function StatusLine({ children }: { children: ReactNode }) {
  return (
    <p className="flex items-center justify-center gap-2 py-6 text-center text-sm text-mist-500" role="status">
      {children}
    </p>
  )
}

/**
 * "Hinzufuegen" in zwei Schritten, wie in der abgestimmten Attrappe: erst der Film bei TMDB,
 * dann seine Fassungen. Steht der Film schon in der Bibliothek, sind seine Fassungen abgehakt
 * und gesperrt, dazu kommt nur, was noch fehlt. Fehlt nichts mehr, fuehrt der Knopf zum Titel,
 * statt "0 Fassungen hinzufuegen" anzubieten.
 *
 * Beim ersten Mal ist nichts vorausgewaehlt, danach die letzte Wahl (`nexcrate.addVersions`).
 * Ohne TMDB-Token steht statt der Treffer die Anleitung mit dem Formular da.
 *
 * Mit `kind="series"` derselbe Weg fuer eine Serie: Je Fassung waehlt man dazu die Regel, der Server
 * zaehlt live, was sie ueberwachen wuerde (`SeriesChoices`). Steht die Serie schon da, fuehrt der
 * Dialog zu ihr, weitere Fassungen kommen auf ihrer Seite dazu.
 */
export function AddDialog({ onClose, kind = 'movie' }: { onClose: () => void; kind?: 'movie' | 'series' }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const navigate = useNavigate()
  const series = kind === 'series'
  const { versions, error: versionsError, reload: reloadVersions } = useVersions(kind)

  const [tmdb, setTmdb] = useState<TmdbState | null>(null)
  const [tmdbError, setTmdbError] = useState<unknown>(null)
  // Ein abgelehnter Token: Der Dialog zeigt den Fehler und das Formular fuer einen neuen.
  const [tokenProblem, setTokenProblem] = useState<unknown>(null)

  const [query, setQuery] = useState('')
  const [year, setYear] = useState('')
  const [results, setResults] = useState<TmdbSearch | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<unknown>(null)
  // Nur die neueste Suche zaehlt. Eine langsame alte Antwort ueberschreibt keine neuere.
  const generation = useRef(0)

  const [picked, setPicked] = useState<TmdbResult | null>(null)
  const [chosen, setChosen] = useState<number[]>([])
  // Serien: je Fassung Haken, Regel und Staffel; `daily` null folgt dem Vorschlag von TMDB.
  const [picks, setPicks] = useState<Record<number, SeriesPick>>({})
  const [daily, setDaily] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [tagText, setTagText] = useState('')
  const [problem, setProblem] = useState<unknown>(null)
  // Library from disk (L8): a result that already lies in a folder nobody knows can be assigned instead of added.
  const [assigning, setAssigning] = useState<{ entry: OnDiskFolder; movie: TmdbResult } | null>(null)
  const notify = useNotice()

  const q = query.trim()
  const searchYear = cleanYear(year)
  const ready = tmdb?.configured === true && tokenProblem === null

  useEffect(() => {
    let current = true
    tmdbApi.state().then(
      (state) => current && setTmdb(state),
      (error: unknown) => current && setTmdbError(error),
    )
    return () => {
      current = false
    }
  }, [])

  // Getippt wird sofort ins Feld. Zu TMDB geht es erst nach einer Pause.
  useEffect(() => {
    const run = ++generation.current
    if (!ready || q === '') {
      setResults(null)
      setSearching(false)
      setSearchError(null)
      return
    }
    const abort = new AbortController()
    const timer = window.setTimeout(() => {
      setSearching(true)
      setSearchError(null)
      tmdbApi.search({ q, year: searchYear, kind }, abort.signal).then(
        (found) => {
          if (run !== generation.current) return
          setResults(found)
          setSearching(false)
        },
        (error: unknown) => {
          if (run !== generation.current || abort.signal.aborted) return
          setSearching(false)
          const trouble = tokenTrouble(error)
          if (trouble === 'missing') setTmdb({ configured: false, checked_at: null })
          else if (trouble === 'rejected') setTokenProblem(error)
          else setSearchError(error)
        },
      )
    }, ADD_SEARCH_DELAY_MS)
    return () => {
      window.clearTimeout(timer)
      abort.abort()
    }
  }, [ready, q, searchYear, kind])

  const existing = picked?.version_ids ?? []
  const free = (versions ?? []).filter((version) => !existing.includes(version.id))
  const selected = series
    ? free.filter((version) => picks[version.id]?.on === true).map((version) => version.id)
    : chosen.filter((id) => free.some((version) => version.id === id))

  // Gezaehlt werden alle freien Fassungen, auch ohne Haken: So stehen Staffeln und Folgen sofort da.
  const previewBody: SeriesAdd | null =
    series && ready && picked !== null && picked.title_id === null && free.length > 0
      ? { kind: 'series', tmdb_id: picked.tmdb_id, versions: free.map((version) => choiceOf(version.id, picks[version.id])) }
      : null
  const { preview, counting, error: previewError } = useSeriesPreview(previewBody)
  const proposedDaily = preview?.proposed_type === 'daily'

  // "Ab Staffel" ohne Staffel beginnt bei der letzten, sobald der Server sie genannt hat. Danach zaehlt er einmal neu.
  useEffect(() => {
    const last = preview?.seasons ?? 0
    if (last < 1) return
    setPicks((current) => {
      const open = Object.entries(current).filter(([, entry]) => entry.rule === 'from_season' && entry.from === null)
      if (open.length === 0) return current
      return { ...current, ...Object.fromEntries(open.map(([id, entry]) => [id, { ...entry, from: last }])) }
    })
  }, [preview])

  // Auch das Zaehlen braucht den Token. Fehlt er, geht es zurueck zur Anleitung wie bei der Suche.
  useEffect(() => {
    const trouble = tokenTrouble(previewError)
    if (trouble === null) return
    if (trouble === 'missing') setTmdb({ configured: false, checked_at: null })
    else setTokenProblem(previewError)
    setPicked(null)
  }, [previewError])

  function tokenSaved(state: TmdbState) {
    setTmdb(state)
    setTokenProblem(null)
  }

  function pick(result: TmdbResult) {
    if (series) {
      // Die letzte Wahl mit ihren Regeln. Die Staffel gehoerte zu einer anderen Serie und bleibt offen.
      const remembered = (readSeriesChoice() ?? []).filter((choice) => !result.version_ids.includes(choice.version_id))
      setPicks(Object.fromEntries(remembered.map((choice) => [choice.version_id, { on: true, rule: choice.rule, from: null }])))
      setDaily(null)
    } else {
      // Beim ersten Mal steht nichts im Speicher, dann ist nichts vorausgewaehlt.
      const remembered = readAddChoice() ?? []
      setChosen(remembered.filter((id) => !result.version_ids.includes(id)))
    }
    setPicked(result)
    setProblem(null)
  }

  function toggle(id: number, on: boolean) {
    setChosen((current) => (on ? [...current.filter((entry) => entry !== id), id] : current.filter((entry) => entry !== id)))
  }

  function changePick(id: number, next: SeriesPick) {
    // "Ab Staffel" beginnt bei der letzten Staffel, wenn der Server sie schon genannt hat.
    const last = preview?.seasons ?? 0
    const filled = next.rule === 'from_season' && next.from === null && last > 0 ? { ...next, from: last } : next
    setPicks((current) => ({ ...current, [id]: filled }))
  }

  function close() {
    if (!busy) onClose()
  }

  function back() {
    if (busy) return
    setPicked(null)
    setProblem(null)
  }

  function openTitle(id: number) {
    onClose()
    navigate(`/titel/${id}`)
  }

  /** Seit T1: die Tags des Feldes, nur hinzugefuegt, damit ein Titel, der schon da ist, keinen verliert. */
  async function addTags(kind: 'movie' | 'series', id: number) {
    const names = tagText.split(',').map((item) => item.trim()).filter((item) => item !== '')
    if (names.length > 0) await tagsApi.change({ kind, ids: [id], add: names })
  }

  async function submit() {
    if (!picked || busy || selected.length === 0) return
    setBusy(true)
    setProblem(null)
    try {
      if (series) {
        const choices = selected.map((id) => choiceOf(id, picks[id]))
        const detail = await libraryApi.add({
          kind: 'series',
          tmdb_id: picked.tmdb_id,
          versions: choices,
          series_type: daily === null ? null : daily ? 'daily' : 'standard',
        })
        storeSeriesChoice(choices)
        await addTags('series', detail.id)
        onClose()
        navigate(`/titel/${detail.id}`)
        return
      }
      // Steht der Film schon da, kommen nur die fehlenden Fassungen dazu. Ein zweites Anlegen gaebe 409 title_exists.
      const detail =
        picked.title_id !== null
          ? await libraryApi.changeVersions(picked.title_id, { add: selected, remove: [] })
          : await libraryApi.add({ kind: 'movie', tmdb_id: picked.tmdb_id, version_ids: selected })
      storeAddChoice([...existing.filter((id) => (versions ?? []).some((version) => version.id === id)), ...selected])
      await addTags('movie', detail.id)
      onClose()
      navigate(`/titel/${detail.id}`)
    } catch (error) {
      setBusy(false)
      const trouble = tokenTrouble(error)
      if (trouble !== null) {
        if (trouble === 'missing') setTmdb({ configured: false, checked_at: null })
        else setTokenProblem(error)
        setPicked(null)
        return
      }
      // Die Liste der Fassungen ist veraltet, etwa weil eine inzwischen umgebaut wurde.
      if (error instanceof ApiError && error.code === 'version_kind_mismatch') reloadVersions()
      setProblem(error)
    }
  }

  /** The stored scan rows of a result that lie in a folder nobody knows, each with its id, root and name. */
  const onDiskOf = (result: TmdbResult): OnDiskFolder[] =>
    !series && Array.isArray(result.on_disk)
      ? result.on_disk.filter((entry) => entry && typeof entry.folder_id === 'number' && typeof entry.name === 'string')
      : []

  const metaOf = (result: TmdbResult) =>
    [
      result.year !== null && result.year > 0 ? String(result.year) : null,
      result.original_title && result.original_title !== result.title ? t('library.add.originalTitle', { title: result.original_title }) : null,
    ]
      .filter(Boolean)
      .join(' · ')

  // title_exists: Ein anderer Weg war schneller, der Titel steht schon da.
  const existingTitle =
    problem instanceof ApiError && problem.code === 'title_exists' && typeof problem.values.title_id === 'number' ? problem.values.title_id : null
  const searchLabel = series ? t('series.add.search') : t('library.add.search')

  let body: ReactNode
  if (tmdb === null) {
    body =
      tmdbError !== null ? (
        <FormMessage>{errorText(t, tmdbError)}</FormMessage>
      ) : (
        <StatusLine>
          <Spinner />
          {t('common.loading')}
        </StatusLine>
      )
  } else if (!tmdb.configured || tokenProblem !== null) {
    body = (
      <div className="flex flex-col gap-4">
        {tokenProblem !== null ? <FormMessage>{errorText(t, tokenProblem)}</FormMessage> : <p className="text-sm text-mist-300">{t('tmdb.needed')}</p>}
        <TmdbTokenForm configured={tmdb.configured} onSaved={tokenSaved} />
      </div>
    )
  } else if (picked === null) {
    body = (
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-[minmax(0,1fr)_5.5rem] gap-2 sm:grid-cols-[minmax(0,1fr)_7rem] sm:gap-3">
          <div className="relative min-w-0">
            <Symbol name="search" className="pointer-events-none absolute top-1/2 left-3.5 h-4 w-4 -translate-y-1/2 text-mist-600" />
            <input
              autoFocus
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={searchLabel}
              aria-label={searchLabel}
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
            placeholder={t('library.add.year')}
            aria-label={t('library.add.year')}
            className="w-full min-w-0 rounded-full border border-ink-700 bg-ink-900 px-4 py-2.5 text-sm text-mist-100 tabular-nums placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
          />
        </div>

        {searchError !== null && <FormMessage>{errorText(t, searchError)}</FormMessage>}
        {q === '' ? (
          <StatusLine>{series ? t('series.add.idle') : t('library.add.idle')}</StatusLine>
        ) : results === null ? (
          searchError === null && (
            <StatusLine>
              <Spinner />
              {t('library.add.searching')}
            </StatusLine>
          )
        ) : results.results.length === 0 ? (
          <StatusLine>{searching ? t('library.add.searching') : t('library.add.noResults')}</StatusLine>
        ) : (
          <div className="flex flex-col gap-2">
            <ul className={'flex flex-col gap-2 transition-opacity ' + (searching ? 'opacity-60' : '')} aria-busy={searching}>
              {results.results.map((result) => {
                const meta = metaOf(result)
                const onDisk = onDiskOf(result)
                return (
                  <li key={result.tmdb_id} className="flex flex-col gap-1.5">
                    <button
                      type="button"
                      onClick={() => pick(result)}
                      className="flex w-full items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-2.5 text-left transition-colors hover:border-accent-500/50 hover:bg-ink-800"
                    >
                      <PosterImage url={result.poster_url} className="w-11 shrink-0" />
                      <span className="flex min-w-0 flex-1 flex-col gap-1">
                        <span className="truncate font-semibold text-mist-100">{result.title}</span>
                        {meta && <span className="truncate text-xs text-mist-500">{meta}</span>}
                        {result.title_id !== null && (
                          <span>
                            <Badge tone="ok">
                              <Symbol name="check" className="h-3.5 w-3.5" />
                              {t('library.add.inLibrary')}
                            </Badge>
                          </span>
                        )}
                      </span>
                      <Symbol name="chevron" className="h-4 w-4 shrink-0 text-mist-600" />
                    </button>
                    {onDisk.map((entry) => (
                      <p key={entry.folder_id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-accent-500/40 bg-accent-500/5 px-3 py-2 text-sm text-mist-200">
                        <span className="flex min-w-0 items-start gap-2">
                          <Symbol name="folder" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
                          <span className="min-w-0 wrap-anywhere">{t('library.add.onDisk', { name: entry.name })}</span>
                        </span>
                        <Button size="sm" variant="ghost" onClick={() => setAssigning({ entry, movie: result })} aria-label={t('library.add.onDiskAssignLabel', { name: entry.name })}>
                          {t('library.add.onDiskAssign')}
                        </Button>
                      </p>
                    ))}
                  </li>
                )
              })}
            </ul>
            {results.total > results.results.length && (
              <p className="text-xs text-mist-500">
                {t('library.add.more', { shown: formatNumber(results.results.length, language), total: formatNumber(results.total, language) })}
              </p>
            )}
          </div>
        )}
      </div>
    )
  } else {
    const meta = metaOf(picked)
    body = (
      <div className="flex flex-col gap-5">
        <div className="flex items-center gap-4">
          <PosterImage url={picked.poster_url} className="w-16 shrink-0" />
          <div className="min-w-0">
            <p className="text-lg font-bold wrap-anywhere">{picked.title}</p>
            {meta && <p className="text-sm wrap-anywhere text-mist-500">{meta}</p>}
            {series && <SeriesFacts preview={preview} counting={counting} />}
          </div>
        </div>
        {picked.overview && <p className="line-clamp-3 text-sm text-mist-400">{picked.overview}</p>}
        <div>
          <p className="font-semibold">{t('library.add.pickVersions')}</p>
          <p className="mt-1 text-sm text-mist-500">{series ? t('series.add.pickVersionsHint') : t('library.add.pickVersionsHint')}</p>
        </div>
        {versionsError !== null && <FormMessage>{errorText(t, versionsError)}</FormMessage>}
        {previewError !== null && tokenTrouble(previewError) === null && <FormMessage>{errorText(t, previewError)}</FormMessage>}
        {versions === null ? (
          versionsError === null && (
            <StatusLine>
              <Spinner />
              {t('common.loading')}
            </StatusLine>
          )
        ) : versions.length === 0 ? (
          <div className="flex flex-col items-start gap-3 rounded-xl border border-dashed border-ink-600 p-4">
            <p className="text-sm text-mist-300">{series ? t('series.add.noVersions') : t('library.add.noVersions')}</p>
            <Link
              to={series ? SERIES_VERSIONS_TAB_PATH : VERSIONS_TAB_PATH}
              onClick={onClose}
              className="inline-flex items-center gap-2 text-sm font-semibold text-accent-400 hover:underline"
            >
              {t('library.add.noVersionsAction')}
              <Symbol name="arrow" />
            </Link>
          </div>
        ) : series ? (
          <SeriesChoices
            title={picked.title}
            versions={versions}
            existing={existing}
            inLibrary={picked.title_id !== null}
            picks={picks}
            onPick={changePick}
            preview={preview}
            counting={counting}
            daily={daily ?? proposedDaily}
            onDaily={setDaily}
            busy={busy}
          />
        ) : (
          <>
            <ul className="flex flex-col gap-2">
              {versions.map((version) => {
                const already = existing.includes(version.id)
                const checked = already || selected.includes(version.id)
                return (
                  <li key={version.id}>
                    <label
                      className={
                        'flex items-center gap-3 rounded-xl border p-3 ' +
                        (already
                          ? 'cursor-default border-ink-700 bg-ink-900/40'
                          : checked
                            ? 'cursor-pointer border-accent-500/50 bg-accent-500/5'
                            : 'cursor-pointer border-ink-700 bg-ink-900/60')
                      }
                    >
                      <input
                        type="checkbox"
                        className="h-4 w-4 shrink-0 accent-accent-500"
                        checked={checked}
                        disabled={already || busy}
                        onChange={(event) => toggle(version.id, event.target.checked)}
                      />
                      <span className="flex min-w-0 flex-1 flex-wrap items-center gap-2 font-semibold text-mist-100">
                        <span className="wrap-anywhere">{version.label}</span>
                        {already && <Badge tone="ok">{t('library.add.exists')}</Badge>}
                      </span>
                    </label>
                  </li>
                )
              })}
            </ul>
            {free.length === 0 ? (
              <FormMessage tone="info">{t('library.add.allExist', { title: picked.title })}</FormMessage>
            ) : (
              <FormMessage tone="info">{t('library.add.searchLater')}</FormMessage>
            )}
          </>
        )}
        {picked !== null && (
        <label className="flex flex-col gap-1.5 text-sm font-medium text-mist-300">
          {t('tags.label')}
          <input
            value={tagText}
            onChange={(event) => setTagText(event.target.value)}
            placeholder={t('tags.addHint')}
            className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
          />
        </label>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        {existingTitle !== null && (
          <div>
            <Button variant="ghost" size="sm" onClick={() => openTitle(existingTitle)}>
              {t('library.add.openTitle')}
              <Symbol name="arrow" />
            </Button>
          </div>
        )}
      </div>
    )
  }

  let footer: ReactNode = undefined
  if (picked !== null && tmdb?.configured === true && tokenProblem === null) {
    const titleId = picked.title_id
    // Eine Serie, die schon da ist, bekommt weitere Fassungen auf ihrer Seite. Der Knopf fuehrt dorthin.
    const leadToTitle = titleId !== null && (series || free.length === 0)
    footer = (
      <>
        <Button variant="ghost" onClick={back} disabled={busy}>
          <Symbol name="back" />
          {t('library.add.back')}
        </Button>
        {versions !== null &&
          versions.length > 0 &&
          (leadToTitle ? (
            <Button onClick={() => openTitle(titleId)}>
              {t('library.add.openTitle')}
              <Symbol name="arrow" />
            </Button>
          ) : (
            <Button onClick={() => void submit()} loading={busy} disabled={selected.length === 0}>
              {selected.length === 0 ? t('library.add.submitNone') : t('library.add.submit', { count: selected.length })}
            </Button>
          ))}
      </>
    )
  }

  const dialogTitle = picked ? t('library.add.pickVersionsTitle', { title: picked.title }) : series ? t('series.add.title') : t('library.add.title')

  return (
    <>
      <Dialog open wide title={dialogTitle} onClose={close} footer={footer}>
        {body}
      </Dialog>
      {assigning !== null && (
        <AssignFromDisk
          entry={assigning.entry}
          movie={assigning.movie}
          onClose={() => setAssigning(null)}
          onAssigned={(detail) => {
            setAssigning(null)
            notify(t('disk.assign.done', { name: assigning.entry.name }))
            onClose()
            navigate(`/titel/${detail.id}`)
          }}
        />
      )}
    </>
  )
}

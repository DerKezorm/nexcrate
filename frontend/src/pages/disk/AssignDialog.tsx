import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError, errorText } from '../../api/client'
import { diskApi } from '../../api/disk'
import { libraryApi } from '../../api/library'
import { cleanYear, tmdbApi } from '../../api/tmdb'
import type { DiskFolder, DiskMediaResult, DiskRoot, TitleDetail, TitleVersion, TmdbResult } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { PosterImage } from '../../components/PosterImage'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Spinner, Toggle } from '../../components/ui'
import { formatList } from '../../lib/format'
import { mediaSummary } from '../../lib/media'
import { languageText } from '../../lib/names'
import { sizeText } from '../../lib/size'
import { TMDB_TAB_PATH, VERSIONS_TAB_PATH } from '../settings/tabs'
import { definitionOf, isFromSource } from '../title/versionDefinitions'
import { useVersions } from '../versions/useVersions'
import { fromText, largestVideo } from './diskText'
import { pickedFromProposal, pickedFromResult, type PickedMovie } from './pickedMovie'

/** Gesucht wird erst, wenn so lange nichts getippt wurde. */
export const ASSIGN_SEARCH_DELAY_MS = 300

/** A version of the chosen movie that has a file, or that a source feeds, cannot take this folder (decision 20). */
function takenBy(version: TitleVersion): 'file' | 'source' | null {
  if (isFromSource(version)) return 'source'
  const hasFile = (typeof version.relative_path === 'string' && version.relative_path !== '') || version.state === 'available' || version.state === 'upgrade'
  return hasFile ? 'file' : null
}

function Heading({ children }: { children: ReactNode }) {
  return <h3 className="text-sm font-semibold text-mist-100">{children}</h3>
}

/**
 * "{{name}} zuordnen" (L6): the file when the folder holds several videos, the media data nexcrate reads from it and
 * the quality it would record, the movie from the proposals or TMDB's search, the version, and "Zuordnen". Nothing on
 * disk changes but the `release.nex` nexcrate puts into the folder afterwards.
 */
export function AssignDialog({
  folder,
  root,
  movie,
  onClose,
  onAssigned,
}: {
  folder: DiskFolder
  /** The root the folder lies in. Its versions decide whether the version is chosen already. null when unknown. */
  root: DiskRoot | null
  /** The movie chosen before opening: the first proposal, or the movie of the add dialog. */
  movie: PickedMovie | null
  onClose: () => void
  onAssigned: (detail: TitleDetail) => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const { versions, error: versionsError, reload: reloadVersions } = useVersions('movie')
  const videos = Array.isArray(folder.videos) ? folder.videos.filter((video) => video && typeof video.name === 'string') : []
  const proposals = Array.isArray(folder.proposals) ? folder.proposals : []
  const rootVersions = root?.versions ?? []

  const [file, setFile] = useState<string | null>(largestVideo(folder)?.name ?? null)
  const [media, setMedia] = useState<DiskMediaResult | null>(null)
  const [mediaError, setMediaError] = useState<unknown>(null)
  const mediaRun = useRef(0)

  const [query, setQuery] = useState(folder.parsed?.title ?? '')
  const [year, setYear] = useState(folder.parsed?.year ? String(folder.parsed.year) : '')
  const [results, setResults] = useState<TmdbResult[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<unknown>(null)
  const searchRun = useRef(0)

  const [picked, setPicked] = useState<PickedMovie | null>(movie)
  const [detail, setDetail] = useState<TitleDetail | null>(null)
  const [versionId, setVersionId] = useState<number | null>(rootVersions.length === 1 ? rootVersions[0].id : null)
  const [busy, setBusy] = useState(false)
  // Ab Werk an (24.09.2026): Eingelesenes loest keine Verbesserungen aus.
  const [keep, setKeep] = useState(true)
  const [problem, setProblem] = useState<unknown>(null)

  const q = query.trim()
  const searchYear = cleanYear(year)

  // The media data of the chosen file, read once per file. The dialog shows the result before anything is recorded.
  useEffect(() => {
    if (file === null) return
    const run = ++mediaRun.current
    setMedia(null)
    setMediaError(null)
    diskApi.media(folder.id, file).then(
      (result) => {
        if (run === mediaRun.current) setMedia(result)
      },
      (error: unknown) => {
        if (run === mediaRun.current) setMediaError(error)
      },
    )
  }, [folder.id, file])

  // TMDB's search, as in "Film hinzufügen": typed at once, sent after a pause.
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

  // A movie already in the library: its versions say which ones have a file or come from Radarr.
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
  const taken = new Map<number, 'file' | 'source'>()
  for (const version of detail?.versions ?? []) {
    const id = definitionOf(version, definitions)
    const reason = takenBy(version)
    if (id !== null && reason !== null) taken.set(id, reason)
  }
  const fixed = rootVersions.length === 1 && !taken.has(rootVersions[0].id) ? rootVersions[0] : null
  const chosenVersion = versionId !== null && !taken.has(versionId) ? versionId : null
  const canSubmit = picked !== null && chosenVersion !== null && file !== null && !busy

  const noTmdb = searchError instanceof ApiError && (searchError.code === 'tmdb_not_configured' || searchError.code === 'tmdb_token_rejected' || searchError.code === 'tmdb_token_unreadable')
  const proposalIds = new Set(proposals.map((proposal) => proposal.tmdb_id))
  const tmdbResults = (results ?? []).filter((result) => !proposalIds.has(result.tmdb_id))

  function choose(next: PickedMovie) {
    setPicked((current) => (current?.tmdb_id === next.tmdb_id ? current : next))
    setProblem(null)
  }

  function close() {
    if (!busy) onClose()
  }

  async function submit() {
    if (!canSubmit || picked === null || chosenVersion === null || file === null) return
    setBusy(true)
    setProblem(null)
    try {
      onAssigned(await diskApi.assign(folder.id, { tmdb_id: picked.tmdb_id, version_id: chosenVersion, file, keep_as_is: keep }))
    } catch (error) {
      setBusy(false)
      setProblem(error)
      // The list of versions is stale, for example because one was rebuilt meanwhile.
      if (error instanceof ApiError && error.code === 'version_kind_mismatch') reloadVersions()
    }
  }

  const summary = media?.media ? mediaSummary(media.media, language, t('title.version.bit')) : null
  const languages = media && Array.isArray(media.languages) ? media.languages.filter((name) => typeof name === 'string' && name !== '') : []

  const movieButton = (candidate: PickedMovie, meta: string | null, key: string) => {
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
        <section className="flex flex-col gap-2" aria-label={t('disk.assign.file')}>
          <Heading>{videos.length > 1 ? t('disk.assign.file') : t('disk.assign.fileOne')}</Heading>
          {videos.length > 1 ? (
            <ul className="flex flex-col gap-1.5">
              {videos.map((video) => (
                <li key={video.name}>
                  <label className={'flex cursor-pointer items-start gap-3 rounded-xl border p-2.5 ' + (file === video.name ? 'border-accent-500/50 bg-accent-500/5' : 'border-ink-700 bg-ink-900/60')}>
                    <input type="radio" name="assign-file" className="mt-1 h-4 w-4 shrink-0 accent-accent-500" checked={file === video.name} onChange={() => setFile(video.name)} disabled={busy} />
                    <span className="flex min-w-0 flex-col">
                      <span className="font-mono text-xs leading-5 wrap-anywhere text-mist-200">{video.name}</span>
                      <span className="text-xs text-mist-500 tabular-nums">{sizeText(t, video.size_bytes, language)}</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          ) : (
            videos[0] && (
              <p className="flex min-w-0 flex-col text-xs">
                <span className="font-mono leading-5 wrap-anywhere text-mist-200">{videos[0].name}</span>
                <span className="text-mist-500 tabular-nums">{sizeText(t, videos[0].size_bytes, language)}</span>
              </p>
            )
          )}
          {file !== null && (
            <div className="flex flex-col gap-1 rounded-xl border border-ink-700 bg-ink-850 px-3.5 py-2.5 text-sm">
              {media === null && mediaError === null && (
                <p className="flex items-center gap-2 text-mist-500" role="status">
                  <Spinner />
                  {t('disk.assign.reading')}
                </p>
              )}
              {mediaError !== null && <p className="text-bad-500">{errorText(t, mediaError)}</p>}
              {media !== null && (
                <>
                  {media.error_code !== null ? (
                    <p className="text-mist-300">{t(media.error_code === 'media_truncated' ? 'disk.assign.truncated' : 'disk.assign.unreadable')}</p>
                  ) : (
                    summary !== null && <p className="text-mist-200">{t('disk.assign.media', { summary })}</p>
                  )}
                  {media.quality !== null && (
                    <p className="flex flex-wrap items-baseline gap-x-2 text-mist-100">
                      <span className="font-semibold">{t('disk.assign.quality', { quality: media.quality })}</span>
                      {media.error_code === null && <span className="text-xs text-mist-500">{media.quality_from === 'media' ? t('disk.assign.fromMedia') : t('disk.assign.fromName')}</span>}
                    </p>
                  )}
                  {languages.length > 0 && <p className="text-xs text-mist-500">{t('disk.assign.languages', { languages: formatList(languages.map((name) => languageText(t, name)), language) })}</p>}
                </>
              )}
            </div>
          )}
        </section>

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
          {proposals.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <h4 className="text-xs font-semibold text-mist-400">{t('disk.assign.proposals')}</h4>
              <ul aria-label={t('disk.assign.proposals')} className="flex flex-col gap-1.5">
                {proposals.map((proposal, index) => movieButton(pickedFromProposal(proposal), fromText(t, proposal.from), `proposal-${proposal.tmdb_id}-${index}`))}
              </ul>
            </div>
          )}
          {picked !== null && picked.from === null && !proposalIds.has(picked.tmdb_id) && !tmdbResults.some((result) => result.tmdb_id === picked.tmdb_id) && (
            <ul aria-label={t('disk.assign.chosen')} className="flex flex-col gap-1.5">
              {movieButton(picked, null, `picked-${picked.tmdb_id}`)}
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
                    result.original_title && result.original_title !== result.title ? t('library.add.originalTitle', { title: result.original_title }) : null,
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
          ) : fixed !== null ? (
            <div className="flex flex-col gap-0.5">
              <p className="text-sm font-medium text-mist-100">{t('disk.assign.versionFixed', { label: fixed.label })}</p>
              <p className="text-xs text-mist-500">{t('disk.assign.versionFixedHint')}</p>
            </div>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {versions.map((version) => {
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
                        name="assign-version"
                        className="h-4 w-4 shrink-0 accent-accent-500"
                        checked={checked}
                        disabled={reason !== null || busy}
                        onChange={() => setVersionId(version.id)}
                      />
                      <span className="flex min-w-0 flex-1 flex-wrap items-center gap-2 text-sm font-semibold text-mist-100">
                        <span className="wrap-anywhere">{version.label}</span>
                        {reason === 'file' && <Badge tone="neutral">{t('disk.assign.hasFile')}</Badge>}
                        {reason === 'source' && <Badge tone="neutral">{t('disk.assign.fromSource')}</Badge>}
                      </span>
                    </label>
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        <p className="flex items-start gap-2 text-sm text-mist-400">
          <Symbol name="info" className="mt-0.5 h-4 w-4 shrink-0 text-info-500" />
          <span>{t('disk.assign.companion')}</span>
        </p>
        {!canSubmit && !busy && (picked === null || chosenVersion === null) && versions !== null && versions.length > 0 && <p className="text-xs text-mist-500">{t('disk.assign.missingChoice')}</p>}
        <Toggle label={t('disk.keep.label')} hint={t('disk.keep.hint')} checked={keep} onChange={setKeep} disabled={busy} />
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

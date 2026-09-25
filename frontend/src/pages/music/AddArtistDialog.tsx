import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import type { TFunction } from 'i18next'

import { errorText } from '../../api/client'
import { musicApi } from '../../api/music'
import { tagsApi } from '../../api/tags'
import type { ArtistHit, ArtistMonitor, ArtistPreview } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Spinner } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { ALBUM_GROUPS, groupLabel } from './groupText'

/** Titel und Erklaerung einer Wahl beim Hinzufuegen (Entscheidung 32), als Schluessel woertlich, nicht aus einer Vorlage. */
function monitorText(t: TFunction, value: ArtistMonitor): { title: string; hint: string } {
  switch (value) {
    case 'studio':
      return { title: t('music.addArtist.monitor.studio.title'), hint: t('music.addArtist.monitor.studio.hint') }
    case 'future':
      return { title: t('music.addArtist.monitor.future.title'), hint: t('music.addArtist.monitor.future.hint') }
    case 'none':
      return { title: t('music.addArtist.monitor.none.title'), hint: t('music.addArtist.monitor.none.hint') }
    case 'missing':
      return { title: t('music.addArtist.monitor.missing.title'), hint: t('music.addArtist.monitor.missing.hint') }
    case 'existing':
      return { title: t('music.addArtist.monitor.existing.title'), hint: t('music.addArtist.monitor.existing.hint') }
    case 'first':
      return { title: t('music.addArtist.monitor.first.title'), hint: t('music.addArtist.monitor.first.hint') }
    case 'latest':
      return { title: t('music.addArtist.monitor.latest.title'), hint: t('music.addArtist.monitor.latest.hint') }
  }
}

/** Die Wahlen in ihrer Reihenfolge; die ersten drei sind die gewohnten, die anderen kennt man aus Lidarr. */
const MONITOR_CHOICES: readonly ArtistMonitor[] = ['studio', 'future', 'none', 'missing', 'existing', 'first', 'latest']
/** Ab Werk zaehlen Studioalben und EPs (Entscheidung 25). */
const DEFAULT_TYPES: readonly string[] = ['studio', 'ep']

/** Gesucht wird erst, wenn so lange nichts getippt wurde, wie bei "Film hinzufuegen". */
export const ADD_ARTIST_SEARCH_DELAY_MS = 300

function StatusLine({ children }: { children: ReactNode }) {
  return (
    <p className="flex items-center justify-center gap-2 py-6 text-center text-sm text-mist-500" role="status">
      {children}
    </p>
  )
}

/**
 * "Künstler hinzufügen" (Entscheidung 31, M1.5): erst die Suche bei MusicBrainz, dann eine Vorschau mit den Zahlen je
 * Gruppe und wie viele Alben die Wahl "Studioalben und EPs" ueberwachen wuerde, dann die Wahl selbst (Entscheidung
 * 32). Ein Treffer, der schon in der Bibliothek steht, zeigt das mit einer Marke und fuehrt beim Bestaetigen dorthin,
 * statt ein zweites Mal anzulegen (der Server antwortet ohnehin 200 statt 201).
 */
export function AddArtistDialog({ onClose }: { onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const navigate = useNavigate()

  const [query, setQuery] = useState('')
  const [results, setResults] = useState<ArtistHit[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<unknown>(null)
  const generation = useRef(0)

  const [picked, setPicked] = useState<ArtistHit | null>(null)
  const [preview, setPreview] = useState<ArtistPreview | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [previewError, setPreviewError] = useState<unknown>(null)
  const [monitor, setMonitor] = useState<ArtistMonitor>('studio')
  const [types, setTypes] = useState<string[]>([...DEFAULT_TYPES])
  const [showMore, setShowMore] = useState(false)
  const [tagText, setTagText] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  const q = query.trim()

  useEffect(() => {
    const run = ++generation.current
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
      musicApi.searchArtists(q, abort.signal).then(
        (found) => {
          if (run !== generation.current) return
          setResults(found)
          setSearching(false)
        },
        (error: unknown) => {
          if (run !== generation.current || abort.signal.aborted) return
          setSearching(false)
          setSearchError(error)
        },
      )
    }, ADD_ARTIST_SEARCH_DELAY_MS)
    return () => {
      window.clearTimeout(timer)
      abort.abort()
    }
  }, [q])

  async function pick(hit: ArtistHit) {
    setPicked(hit)
    setPreview(null)
    setPreviewError(null)
    setProblem(null)
    setPreviewing(true)
    try {
      setPreview(await musicApi.previewArtist(hit.mbid))
    } catch (error) {
      setPreviewError(error)
    } finally {
      setPreviewing(false)
    }
  }

  function back() {
    if (busy) return
    setPicked(null)
    setPreview(null)
    setPreviewError(null)
    setProblem(null)
  }

  function close() {
    if (!busy) onClose()
  }

  function openArtist(id: number) {
    onClose()
    navigate(`/kuenstler/${id}`)
  }

  async function submit() {
    if (!picked || busy || preview === null) return
    setBusy(true)
    setProblem(null)
    try {
      const sameAsDefault = types.length === DEFAULT_TYPES.length && DEFAULT_TYPES.every((item) => types.includes(item))
      const summary = await musicApi.addArtist({ mbid: picked.mbid, monitor, ...(sameAsDefault ? {} : { types }) })
      const names = tagText.split(',').map((item) => item.trim()).filter((item) => item !== '')
      if (names.length > 0) await tagsApi.change({ kind: 'artist', ids: [summary.id], add: names })
      onClose()
      navigate(`/kuenstler/${summary.id}`)
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  let body: ReactNode
  if (picked === null) {
    body = (
      <div className="flex flex-col gap-4">
        <div className="relative min-w-0">
          <Symbol name="search" className="pointer-events-none absolute top-1/2 left-3.5 h-4 w-4 -translate-y-1/2 text-mist-600" />
          <input
            autoFocus
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('music.addArtist.search')}
            aria-label={t('music.addArtist.search')}
            maxLength={200}
            className="w-full rounded-full border border-ink-700 bg-ink-900 py-2.5 pr-4 pl-10 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
          />
        </div>
        {searchError !== null && <FormMessage>{errorText(t, searchError)}</FormMessage>}
        {q === '' ? (
          <StatusLine>{t('music.addArtist.idle')}</StatusLine>
        ) : results === null ? (
          searchError === null && (
            <StatusLine>
              <Spinner />
              {t('music.addArtist.searching')}
            </StatusLine>
          )
        ) : results.length === 0 ? (
          <StatusLine>{searching ? t('music.addArtist.searching') : t('music.addArtist.noResults')}</StatusLine>
        ) : (
          <ul className={'flex flex-col gap-2 transition-opacity ' + (searching ? 'opacity-60' : '')} aria-busy={searching}>
            {results.map((hit) => (
              <li key={hit.mbid}>
                <button
                  type="button"
                  onClick={() => void pick(hit)}
                  className="flex w-full items-start gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-3 text-left transition-colors hover:border-accent-500/50 hover:bg-ink-800"
                >
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold text-mist-100">{hit.name}</span>
                      {hit.in_library !== null && (
                        <Badge tone="ok">
                          <Symbol name="check" className="h-3.5 w-3.5" />
                          {t('music.addArtist.inLibrary')}
                        </Badge>
                      )}
                    </span>
                    <span className="block truncate text-xs text-mist-500">
                      {[hit.disambiguation, hit.artist_type, hit.country, [hit.begin_year, hit.end_year].filter(Boolean).join('–')].filter(Boolean).join(' · ')}
                    </span>
                  </span>
                  <Symbol name="chevron" className="mt-1 h-4 w-4 shrink-0 text-mist-600" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    )
  } else {
    body = (
      <div className="flex flex-col gap-5">
        <div>
          <p className="text-lg font-bold wrap-anywhere">{picked.name}</p>
          <p className="text-sm wrap-anywhere text-mist-500">
            {[picked.disambiguation, picked.artist_type, picked.country].filter(Boolean).join(' · ')}
          </p>
        </div>
        {previewing ? (
          <StatusLine>
            <Spinner />
            {t('music.addArtist.loadingPreview')}
          </StatusLine>
        ) : previewError !== null ? (
          <FormMessage>{errorText(t, previewError)}</FormMessage>
        ) : preview !== null ? (
          <>
            {preview.in_library !== null ? (
              <FormMessage tone="info">{t('music.addArtist.alreadyThere')}</FormMessage>
            ) : (
              <>
                <div className="flex flex-wrap gap-2" role="list" aria-label={t('music.addArtist.groupCounts')}>
                  {preview.groups
                    .filter((entry) => entry.count > 0)
                    .map((entry) => (
                      <span key={entry.group} className="inline-flex items-center gap-1.5 rounded-full border border-ink-700 bg-ink-900 px-3 py-1 text-sm text-mist-300">
                        {groupLabel(t, entry.group)}
                        <span className="rounded-full bg-accent-500/15 px-1.5 text-xs font-semibold text-accent-400 tabular-nums">{formatNumber(entry.count, language)}</span>
                      </span>
                    ))}
                </div>
                <p className="font-semibold text-mist-100">{t('music.addArtist.wouldWatch', { count: preview.would_watch, value: formatNumber(preview.would_watch, language) })}</p>
                <fieldset className="flex flex-col gap-2">
                  <legend className="sr-only">{t('music.addArtist.monitorLegend')}</legend>
                  {MONITOR_CHOICES.filter((value, index) => showMore || index < 3 || value === monitor).map((value) => (
                    <label
                      key={value}
                      className={'flex cursor-pointer items-start gap-3 rounded-xl border p-3 ' + (monitor === value ? 'border-accent-500/50 bg-accent-500/5' : 'border-ink-700 bg-ink-900/60')}
                    >
                      <input type="radio" name="monitor" className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500" checked={monitor === value} onChange={() => setMonitor(value)} />
                      <span className="flex flex-col">
                        <span className="font-semibold text-mist-100">{monitorText(t, value).title}</span>
                        <span className="text-xs text-mist-500">{monitorText(t, value).hint}</span>
                      </span>
                    </label>
                  ))}
                </fieldset>
                <label className="flex flex-col gap-1.5 text-sm font-medium text-mist-300">
                  {t('tags.label')}
                  <input
                    value={tagText}
                    onChange={(event) => setTagText(event.target.value)}
                    placeholder={t('tags.addHint')}
                    className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
                  />
                </label>
                {!showMore ? (
                  <Button variant="ghost" size="sm" className="self-start" onClick={() => setShowMore(true)}>
                    {t('music.addArtist.more')}
                  </Button>
                ) : (
                  <fieldset className="flex flex-col gap-2">
                    <legend className="mb-1 text-sm font-semibold text-mist-300">{t('music.addArtist.types')}</legend>
                    <p className="text-xs text-mist-500">{t('music.addArtist.typesHint')}</p>
                    <div className="flex flex-wrap gap-2">
                      {ALBUM_GROUPS.map((group) => (
                        <label key={group} className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-ink-700 bg-ink-900 px-3 py-1 text-sm text-mist-300">
                          <input
                            type="checkbox"
                            className="h-4 w-4 accent-accent-500"
                            checked={types.includes(group)}
                            onChange={(event) =>
                              setTypes((current) => (event.target.checked ? [...current, group] : current.filter((item) => item !== group)))
                            }
                          />
                          {groupLabel(t, group)}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                )}
              </>
            )}
          </>
        ) : null}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    )
  }

  const footer =
    picked === null ? undefined : preview?.in_library !== null && preview?.in_library !== undefined ? (
      <>
        <Button variant="ghost" onClick={back} disabled={busy}>
          <Symbol name="back" />
          {t('library.add.back')}
        </Button>
        <Button onClick={() => openArtist(preview.in_library as number)}>
          {t('music.addArtist.openArtist')}
          <Symbol name="arrow" />
        </Button>
      </>
    ) : (
      <>
        <Button variant="ghost" onClick={back} disabled={busy}>
          <Symbol name="back" />
          {t('library.add.back')}
        </Button>
        <Button onClick={() => void submit()} loading={busy} disabled={preview === null || previewing || types.length === 0}>
          {t('music.addArtist.submit')}
        </Button>
      </>
    )

  return (
    <Dialog open title={t('music.addArtist.title')} onClose={close} footer={footer}>
      {body}
    </Dialog>
  )
}

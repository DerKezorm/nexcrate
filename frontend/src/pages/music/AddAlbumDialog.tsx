import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { errorText } from '../../api/client'
import { musicApi } from '../../api/music'
import type { AlbumHit } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Segmented } from '../../components/Segmented'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Spinner } from '../../components/ui'
import { ADD_ARTIST_SEARCH_DELAY_MS } from './AddArtistDialog'

/** Die Wahl im Umschalter der Suche, `all` steht fuer keinen Filter (Entscheidung 34). */
const ALBUM_KIND_CHOICES = ['all', 'album', 'compilation', 'single'] as const
type AlbumKindChoice = (typeof ALBUM_KIND_CHOICES)[number]

/**
 * "Album hinzufügen" (Entscheidung 34): sucht Release-Groups bei MusicBrainz, wahlweise nach Art gefiltert (ohne
 * Filter stehen fremde Singles zuerst, B1), und legt genau dieses Album mit Fassung an. Gehoert es einem Kuenstler,
 * der noch nicht in der Bibliothek steht, legt der Server ihn mit an, ohne weitere Ueberwachung. Derselbe Dialog
 * dient auch fuer eine Single von der Kuenstlerseite (Entscheidung 34, E5).
 */
export function AddAlbumDialog({ onClose, artist }: { onClose: () => void; artist?: { mbid: string; name: string } }) {
  const { t } = useTranslation()
  const navigate = useNavigate()

  const [query, setQuery] = useState('')
  const [kind, setKind] = useState<AlbumKindChoice>('all')
  const [results, setResults] = useState<AlbumHit[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<unknown>(null)
  const generation = useRef(0)

  const [picked, setPicked] = useState<AlbumHit | null>(null)
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
      musicApi.searchAlbums(q, artist?.mbid, kind === 'all' ? undefined : kind, abort.signal).then(
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
  }, [q, kind, artist?.mbid])

  function close() {
    if (!busy) onClose()
  }

  function openTitle(id: number) {
    onClose()
    navigate(`/titel/${id}`)
  }

  async function submit() {
    if (!picked || busy) return
    setBusy(true)
    setProblem(null)
    try {
      const created = await musicApi.addAlbum({ mbid: picked.mbid })
      onClose()
      navigate(`/titel/${created.id}`)
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  const kindLabel = (value: AlbumKindChoice): string =>
    ({ all: t('music.addAlbum.kind.all'), album: t('music.addAlbum.kind.album'), compilation: t('music.addAlbum.kind.compilation'), single: t('music.addAlbum.kind.single') })[value]

  return (
    <Dialog
      open
      title={artist ? t('music.addAlbum.titleFor', { name: artist.name }) : t('music.addAlbum.title')}
      onClose={close}
      footer={
        picked !== null ? (
          <>
            <Button variant="ghost" onClick={() => setPicked(null)} disabled={busy}>
              <Symbol name="back" />
              {t('library.add.back')}
            </Button>
            {picked.in_library !== null ? (
              <Button onClick={() => openTitle(picked.in_library as number)}>
                {t('music.addAlbum.openAlbum')}
                <Symbol name="arrow" />
              </Button>
            ) : (
              <Button onClick={() => void submit()} loading={busy}>
                {t('music.addAlbum.submit')}
              </Button>
            )}
          </>
        ) : undefined
      }
    >
      {picked === null ? (
        <div className="flex flex-col gap-4">
          <div className="relative min-w-0">
            <Symbol name="search" className="pointer-events-none absolute top-1/2 left-3.5 h-4 w-4 -translate-y-1/2 text-mist-600" />
            <input
              autoFocus
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t('music.addAlbum.search')}
              aria-label={t('music.addAlbum.search')}
              maxLength={200}
              className="w-full rounded-full border border-ink-700 bg-ink-900 py-2.5 pr-4 pl-10 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
            />
          </div>
          <Segmented value={kind} options={ALBUM_KIND_CHOICES} onChange={setKind} label={kindLabel} ariaLabel={t('music.addAlbum.kindLabel')} />
          {searchError !== null && <FormMessage>{errorText(t, searchError)}</FormMessage>}
          {q === '' ? (
            <p className="flex items-center justify-center gap-2 py-6 text-center text-sm text-mist-500" role="status">
              {t('music.addAlbum.idle')}
            </p>
          ) : results === null ? (
            searchError === null && (
              <p className="flex items-center justify-center gap-2 py-6 text-center text-sm text-mist-500" role="status">
                <Spinner />
                {t('music.addAlbum.searching')}
              </p>
            )
          ) : results.length === 0 ? (
            <p className="flex items-center justify-center gap-2 py-6 text-center text-sm text-mist-500" role="status">
              {searching ? t('music.addAlbum.searching') : t('music.addAlbum.noResults')}
            </p>
          ) : (
            <ul className={'flex flex-col gap-2 transition-opacity ' + (searching ? 'opacity-60' : '')} aria-busy={searching}>
              {results.map((hit) => (
                <li key={hit.mbid}>
                  <button
                    type="button"
                    onClick={() => setPicked(hit)}
                    className="flex w-full items-start gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-3 text-left transition-colors hover:border-accent-500/50 hover:bg-ink-800"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-mist-100">{hit.title}</span>
                        {hit.in_library !== null && (
                          <Badge tone="ok">
                            <Symbol name="check" className="h-3.5 w-3.5" />
                            {t('music.addAlbum.inLibrary')}
                          </Badge>
                        )}
                      </span>
                      <span className="block truncate text-xs text-mist-500">
                        {[hit.artist, hit.first_release_date?.slice(0, 4)].filter(Boolean).join(' · ')}
                      </span>
                    </span>
                    <Symbol name="chevron" className="mt-1 h-4 w-4 shrink-0 text-mist-600" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-lg font-bold wrap-anywhere">{picked.title}</p>
          <p className="text-sm text-mist-500">{[picked.artist, picked.first_release_date?.slice(0, 4)].filter(Boolean).join(' · ')}</p>
          {picked.in_library !== null ? (
            <FormMessage tone="info">{t('music.addAlbum.alreadyThere')}</FormMessage>
          ) : (
            <p className="text-sm text-mist-400">{t('music.addAlbum.confirmHint')}</p>
          )}
          {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        </div>
      )}
    </Dialog>
  )
}

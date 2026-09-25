import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { libraryApi } from '../../api/library'
import { ARTIST_SORTS, musicApi, type ArtistSort } from '../../api/music'
import type { ArtistSummary, LibrarySort, LibraryStats, TitleSummary } from '../../api/types'
import { Segmented } from '../../components/Segmented'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, Spinner } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { TitleGrid } from '../library/TitleGrid'
import { TitleList } from '../library/TitleList'
import { MUSIC_VIEWS, type LibraryAddress, type MusicView } from '../library/address'
import { TagFilter } from '../../components/TagEditor'
import { SelectionBar } from '../library/SelectionBar'
import { useSelection } from '../library/selection'
import { StateChips } from '../library/StateChips'
import { LIBRARY_VIEWS, type LibraryView } from '../library/viewStore'
import { ArtistGrid } from './ArtistGrid'
import { ArtistRowList } from './ArtistRowList'
import { LoadingBar } from './LoadingBar'
import { useLoadingStatus } from './useLoadingStatus'

const ALBUM_SORTS: readonly LibrarySort[] = ['title', 'year', 'added']

type ArtistListing = { items: ArtistSummary[]; total: number; page: number }
type AlbumListing = { items: TitleSummary[]; total: number; page: number }

/**
 * Der Musik-Bereich der Bibliothek (M1.5): Umschalter Kuenstler/Alben (Entscheidung 37), Suche, Sortierung, die
 * Ladezeile aus `GET /api/music/loading`, und darunter die Liste. Kuenstler kommen von `musicApi`, Alben ueber die
 * vorhandene Titelliste mit `kind=album`, mit demselben Poster- oder Listenbauteil wie Filme und Serien.
 *
 * Die Albenansicht hat seit dem Musik-Abschluss dieselben Zustandsfilter wie Filme und Serien, samt Zahl.
 */
export function MusicLibrary({
  address,
  onChange,
  view,
  onChooseView,
  stats,
}: {
  address: LibraryAddress
  onChange: (change: Partial<LibraryAddress>) => void
  view: LibraryView
  onChooseView: (next: LibraryView) => void
  /** Fuer die Zahlen an den Zustandsfiltern der Alben. */
  stats: LibraryStats | null
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const loadingStatus = useLoadingStatus()
  const musicView = address.view
  const search = address.q.trim()
  const state = address.state === 'all' ? null : address.state

  const [artistListing, setArtistListing] = useState<ArtistListing | null>(null)
  const [albumListing, setAlbumListing] = useState<AlbumListing | null>(null)
  const [loading, setLoading] = useState<'first' | 'more' | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [retries, setRetries] = useState(0)
  const selection = useSelection()
  const generation = useRef(0)

  useEffect(() => {
    const run = ++generation.current
    const abort = new AbortController()
    setLoading('first')
    setError(null)
    if (musicView === 'kuenstler') {
      musicApi.listArtists(search, address.artistSort, 1, abort.signal, address.tag).then(
        (result) => {
          if (run !== generation.current) return
          setArtistListing({ items: result.items, total: result.total, page: 1 })
          setLoading(null)
        },
        (problem: unknown) => {
          if (run !== generation.current || abort.signal.aborted) return
          setError(problem)
          setLoading(null)
        },
      )
    } else {
      libraryApi.list({ kind: 'album', state, q: search, sort: address.sort, page: 1, tag: address.tag }, abort.signal).then(
        (result) => {
          if (run !== generation.current) return
          setAlbumListing({ items: result.items, total: result.total, page: 1 })
          setLoading(null)
        },
        (problem: unknown) => {
          if (run !== generation.current || abort.signal.aborted) return
          setError(problem)
          setLoading(null)
        },
      )
    }
    return () => abort.abort()
  }, [musicView, search, state, address.artistSort, address.sort, address.tag, retries])

  async function loadMore() {
    if (loading !== null) return
    setLoading('more')
    try {
      if (musicView === 'kuenstler' && artistListing !== null) {
        const page = artistListing.page + 1
        const result = await musicApi.listArtists(search, address.artistSort, page, undefined, address.tag)
        const known = new Set(artistListing.items.map((item) => item.id))
        setArtistListing({ items: [...artistListing.items, ...result.items.filter((item) => !known.has(item.id))], total: result.total, page })
      } else if (albumListing !== null) {
        const page = albumListing.page + 1
        const result = await libraryApi.list({ kind: 'album', state, q: search, sort: address.sort, page, tag: address.tag })
        const known = new Set(albumListing.items.map((item) => item.id))
        setAlbumListing({ items: [...albumListing.items, ...result.items.filter((item) => !known.has(item.id))], total: result.total, page })
      }
    } catch (problem) {
      setError(problem)
    } finally {
      setLoading(null)
    }
  }

  function changeView(next: MusicView) {
    // Beim Wechsel passt sich die Sortierung an, ohne den Suchtext zu verlieren.
    onChange({ view: next, page: 1 })
  }

  const viewLabel = (value: LibraryView): string => ({ grid: t('library.view.grid'), list: t('library.view.list') })[value]
  const musicViewLabel = (value: MusicView): string => ({ kuenstler: t('music.view.artists'), alben: t('music.view.albums') })[value]
  const artistSortLabel = (value: ArtistSort): string => ({ name: t('music.sort.name'), added: t('library.sort.added') })[value]
  const albumSortLabel = (value: LibrarySort): string => ({ title: t('library.sort.title'), year: t('library.sort.year'), added: t('library.sort.added') })[value]

  const listing = musicView === 'kuenstler' ? artistListing : albumListing
  const total = listing?.total ?? 0
  const shown = listing?.items.length ?? 0

  return (
    <div className="flex flex-col gap-4">
      <LoadingBar status={loadingStatus} />
      {/* Die Suche steht eine Zeile hoeher, beim Umschalter der Art (Rueckmeldung 20.09.2026). Hier: Kuenstler oder
          Alben und, bei Alben, die Zustandsfilter; rechts Ansicht und Sortierung wie bei Filmen und Serien. */}
      {selection.on && (
        <SelectionBar
          kind="album"
          artists={musicView === 'kuenstler'}
          selection={selection}
          state={address.state}
          q={search}
          tag={address.tag}
          shown={(musicView === 'kuenstler' ? (artistListing?.items ?? []) : (albumListing?.items ?? [])).map((item) => item.id)}
          total={total}
          onDone={() => setRetries((count) => count + 1)}
        />
      )}
      {/* Wie bei Filmen: erst der Umschalter, dann die Zustaende ueber die ganze Breite, dann die Zahl mit
          Ansicht, Sortierung und "Auswaehlen" daneben. */}
      <Segmented value={musicView} options={MUSIC_VIEWS} onChange={changeView} label={musicViewLabel} ariaLabel={t('music.view.label')} />
      {musicView === 'alben' && <StateChips kind="album" value={address.state} stats={stats} onChange={(next) => onChange({ state: next, page: 1 })} />}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
          {listing !== null &&
            (musicView === 'kuenstler'
              ? t('music.artistCount', { count: total, value: formatNumber(total, language) })
              : t('library.count', { count: total, value: formatNumber(total, language) }))}
          {loading === 'first' && <Spinner className="h-3.5 w-3.5" />}
        </p>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-3">
          {/* Rueckmeldung 20.09.2026: markieren geht bei Kuenstlern und Alben genauso. */}
          {!selection.on && (
            <Button variant="ghost" size="sm" onClick={selection.start}>
              <Symbol name="check" />
              {t('library.select.open')}
            </Button>
          )}
        <TagFilter value={address.tag} onChange={(next) => onChange({ tag: next, page: 1 })} />
        <Segmented value={view} options={LIBRARY_VIEWS} onChange={onChooseView} label={viewLabel} ariaLabel={t('library.view.label')} />
        {musicView === 'kuenstler' ? (
          <label className="flex items-center gap-2 text-sm text-mist-500">
            {t('library.sort.label')}
            <select
              value={address.artistSort}
              onChange={(event) => onChange({ artistSort: event.target.value as ArtistSort, page: 1 })}
              className="rounded-full border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
            >
              {ARTIST_SORTS.map((value) => (
                <option key={value} value={value}>
                  {artistSortLabel(value)}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <label className="flex items-center gap-2 text-sm text-mist-500">
            {t('library.sort.label')}
            <select
              value={address.sort}
              onChange={(event) => onChange({ sort: event.target.value as LibrarySort, page: 1 })}
              className="rounded-full border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
            >
              {ALBUM_SORTS.map((value) => (
                <option key={value} value={value}>
                  {albumSortLabel(value)}
                </option>
              ))}
            </select>
          </label>
        )}
        </div>
      </div>

      {error !== null ? (
        <div className="flex flex-col items-start gap-3">
          <FormMessage>{errorText(t, error)}</FormMessage>
          <Button variant="ghost" onClick={() => setRetries((count) => count + 1)}>
            <Symbol name="refresh" />
            {t('common.actions.retry')}
          </Button>
        </div>
      ) : listing === null ? (
        <p className="flex items-center justify-center gap-2 py-16 text-sm text-mist-500" role="status">
          <Spinner />
          {t('common.loading')}
        </p>
      ) : listing.total === 0 ? (
        <p className="rounded-2xl border border-dashed border-ink-700 px-6 py-12 text-center text-sm text-mist-500" role="status">
          {t('library.empty')}
        </p>
      ) : (
        <section className="flex flex-col gap-4" aria-busy={loading === 'first'}>
          <div className={loading === 'first' ? 'opacity-60' : ''}>
            {musicView === 'kuenstler' ? (
              view === 'list' ? (
                <ArtistRowList artists={artistListing?.items ?? []} selection={selection.on ? selection : null} />
              ) : (
                <ArtistGrid artists={artistListing?.items ?? []} selection={selection.on ? selection : null} />
              )
            ) : view === 'list' ? (
              <TitleList titles={albumListing?.items ?? []} selection={selection.on ? selection : null} />
            ) : (
              <TitleGrid titles={albumListing?.items ?? []} selection={selection.on ? selection : null} />
            )}
          </div>
          {shown < total && (
            <div className="flex flex-col items-center gap-2 pt-2">
              <Button variant="ghost" onClick={() => void loadMore()} loading={loading === 'more'} disabled={loading !== null}>
                {t('library.more')}
              </Button>
              <p className="text-xs text-mist-500 tabular-nums">{t('library.shown', { shown: formatNumber(shown, language), total: formatNumber(total, language) })}</p>
            </div>
          )}
        </section>
      )}
    </div>
  )
}

import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigationType, useSearchParams } from 'react-router-dom'

import { errorText } from '../api/client'
import { LIBRARY_PAGE_SIZE, LIBRARY_SORTS, libraryApi, type LibraryQuery } from '../api/library'
import type { LibrarySort, LibraryStats, MediaKind, TitleSummary, VersionState } from '../api/types'
import { Segmented } from '../components/Segmented'
import { Symbol } from '../components/Symbol'
import { TagFilter } from '../components/TagEditor'
import { Button, FormMessage, KeyFigure, PageTitle, Spinner } from '../components/ui'
import { useNotice } from '../components/useNotice'
import { formatNumber } from '../lib/format'
import { sizeText } from '../lib/size'
import { AddDialog } from './library/AddDialog'
import { AddMenu } from './library/AddMenu'
import { KINDS, readAddress, sameAddress, stateFiltersFor, writeAddress, type LibraryAddress } from './library/address'
import { SelectionBar } from './library/SelectionBar'
import { SeriesTypeProposals } from './library/SeriesTypeProposals'
import { useSelection } from './library/selection'
import { StateChips } from './library/StateChips'
import { EmptyLibrary } from './library/EmptyLibrary'
import { TitleGrid } from './library/TitleGrid'
import { TitleList } from './library/TitleList'
import { LIBRARY_VIEWS, storedLibraryView, storeLibraryView, type LibraryView } from './library/viewStore'
import { AddAlbumDialog } from './music/AddAlbumDialog'
import { AddArtistDialog } from './music/AddArtistDialog'
import { MusicLibrary } from './music/MusicLibrary'

/** Gesucht wird erst, wenn so lange nichts getippt wurde. */
export const SEARCH_DELAY_MS = 300

/** `kind`: fuer welche Art die Liste geladen wurde. Beim Wechsel von Filmen zu Serien zeigt die Seite solange nicht die Filme. */
type Listing = { items: TitleSummary[]; total: number; page: number; kind: MediaKind }

/** Neue Seite anhaengen. Hat sich die Bibliothek dazwischen verschoben, steht kein Titel doppelt da. */
function append(previous: Listing, items: TitleSummary[], total: number, page: number): Listing {
  const known = new Set(previous.items.map((item) => item.id))
  return { items: [...previous.items, ...items.filter((item) => !known.has(item.id))], total, page, kind: previous.kind }
}

/**
 * Seite 1 und dahinter so viele, wie die Adresse verlangt, hoechstens bis zur letzten
 * vorhandenen. Erst Seite 1 sagt, wie viele es gibt; die uebrigen gehen dann zusammen los.
 */
async function loadPages(query: Omit<LibraryQuery, 'page'>, wanted: number, signal: AbortSignal): Promise<Listing> {
  const first = await libraryApi.list({ ...query, page: 1 }, signal)
  const pages = Math.min(wanted, Math.max(1, Math.ceil(first.total / LIBRARY_PAGE_SIZE)))
  let listing: Listing = { items: first.items, total: first.total, page: 1, kind: query.kind }
  if (pages > 1) {
    const rest = await Promise.all(Array.from({ length: pages - 1 }, (_, index) => libraryApi.list({ ...query, page: index + 2 }, signal)))
    rest.forEach((result, index) => {
      listing = append(listing, result.items, result.total, index + 2)
    })
  }
  return listing
}

/**
 * Die Bibliothek. Suchen, filtern und sortieren tut der Server, er kennt die
 * Schreibweisen mit und ohne Umlaut. Art, Zustand, Suche, Sortierung und die Zahl der
 * geladenen Seiten stehen in der Adresse: Wer von einem Titel zurueckkommt, findet
 * die Liste so vor, wie er sie verlassen hat. Poster oder Liste merkt sich der Browser.
 * "Hinzufuegen" und "Neu anfangen" oeffnen dieselbe Suche bei TMDB.
 */
export function LibraryPage() {
  const { t, i18n } = useTranslation()
  const notify = useNotice()
  const language = i18n.language
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const navigationType = useNavigationType()
  // ⚠️ Was die Seite zeigt, steht im Zustand und von dort in der Adresse, nicht umgekehrt.
  // Der Router schreibt die Adresse als Transition, einen Schritt spaeter. Baut die zweite von
  // zwei schnellen Aenderungen auf der Adresse auf, geht die erste verloren: In der
  // Sichtpruefung am 13.09.2026 verschwand so die Sortierung beim Klick auf "Mehr laden".
  const [address, setAddress] = useState<LibraryAddress>(() => readAddress(params))
  const { kind, sort } = address
  const stateFilter = address.state
  const search = address.q.trim()
  const tag = address.tag
  const state: VersionState | 'unclear' | null = stateFilter === 'all' ? null : stateFilter

  const [view, setView] = useState<LibraryView>(storedLibraryView)
  // Anime B5: "Art pruefen" fuer die Serien des Bestands.
  const [proposing, setProposing] = useState(false)
  const [searchInput, setSearchInput] = useState(address.q)
  const [listing, setListing] = useState<Listing | null>(null)
  const [loading, setLoading] = useState<'first' | 'more' | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [moreError, setMoreError] = useState<unknown>(null)
  const [retries, setRetries] = useState(0)
  const selection = useSelection()
  const [stats, setStats] = useState<LibraryStats | null>(null)
  const [statsError, setStatsError] = useState<unknown>(null)
  // Welche Suche offen ist. Auf dem Reiter Serien sucht "Hinzufuegen" Serien, sonst Filme.
  const [adding, setAdding] = useState<'movie' | 'series' | null>(null)
  // Musik M1.5: "Kuenstler hinzufuegen" und "Album hinzufuegen" oeffnen je einen eigenen Dialog, kein AddDialog.
  const [addingArtist, setAddingArtist] = useState(false)
  const [addingAlbum, setAddingAlbum] = useState(false)
  // Fuer AddDialog und EmptyLibrary gibt es nur Film oder Serie; Musik hat eigene Dialoge (addingArtist, addingAlbum).
  const movieOrSeries = kind === 'series' ? 'series' : 'movie'
  const addKind = kind === 'album' ? 'music' : movieOrSeries
  // Filme und Serien liest der Server ueber die allgemeine Titelliste, Musik (Kuenstler) ueber MusicLibrary.
  const listed = kind !== 'album'
  // Nur die neueste Anfrage zaehlt. Eine langsame alte Antwort ueberschreibt keine neuere.
  const generation = useRef(0)
  const aborter = useRef<AbortController | null>(null)

  /** Aendert, was die Bibliothek zeigt. Aenderungen kurz hintereinander bauen aufeinander auf. */
  const update = useCallback((change: Partial<LibraryAddress>) => setAddress((previous) => ({ ...previous, ...change })), [])

  // Wie viele Seiten verlangt sind. Gelesen beim Laden, nicht als Ausloeser: "Mehr laden" setzt sie selbst.
  const pagesRef = useRef(address.page)
  pagesRef.current = address.page
  // Was zuletzt in der Adresse stand, von hier geschrieben oder dort vorgefunden. Nicht der
  // Router selbst: Der hinkt waehrend einer Transition hinterher.
  const written = useRef(location.search)
  const writeParams = useRef(setParams)
  writeParams.current = setParams
  // Der Suchtext, der zuletzt im Zustand stand, ob von hier geschrieben oder von aussen gekommen.
  const addressSearch = useRef(search)

  // Die Adresse folgt dem Zustand, ohne Eintrag im Verlauf: "Zurueck" fuehrt dorthin, wo man vor der Bibliothek war.
  useEffect(() => {
    const next = writeAddress(address)
    const text = next.toString() === '' ? '' : `?${next.toString()}`
    if (text === written.current) return
    written.current = text
    writeParams.current(next, { replace: true })
  }, [address])

  // Von aussen neu angesteuert, etwa ueber den Link zur Bibliothek: Der Zustand folgt der Adresse.
  // Die eigenen Aenderungen kommen als REPLACE an und bleiben hier aussen vor.
  useEffect(() => {
    if (navigationType === 'REPLACE') return
    written.current = location.search
    const next = readAddress(new URLSearchParams(location.search))
    setAddress((previous) => (sameAddress(previous, next) ? previous : next))
  }, [location.key, location.search, navigationType])

  useEffect(() => {
    let current = true
    libraryApi.stats().then(
      (result) => current && setStats(result),
      (problem: unknown) => current && setStatsError(problem),
    )
    return () => {
      current = false
    }
  }, [])

  // Der Suchtext aendert sich von aussen: Das Feld zieht nach.
  useEffect(() => {
    if (search === addressSearch.current) return
    addressSearch.current = search
    setSearchInput(search)
  }, [search])

  // Getippt wird sofort ins Feld. In den Zustand und an den Server geht es erst nach einer Pause.
  useEffect(() => {
    const next = searchInput.trim()
    if (next === addressSearch.current) return
    const timer = window.setTimeout(() => {
      addressSearch.current = next
      update({ q: next, page: 1 })
    }, SEARCH_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [searchInput, update])

  useEffect(() => {
    const run = ++generation.current
    // Musik liest der Server noch nicht, dafuer gibt es keine Anfrage.
    if (kind === 'album') return
    const abort = new AbortController()
    aborter.current = abort
    setLoading('first')
    setError(null)
    setMoreError(null)
    // Beim Zurueckkommen verlangt die Adresse vielleicht mehr als eine Seite.
    const wanted = pagesRef.current
    loadPages({ kind, state, q: search, sort, tag }, wanted, abort.signal).then(
      (result) => {
        if (run !== generation.current) return
        setListing(result)
        setLoading(null)
        // Die Adresse verlangte mehr Seiten, als es gibt, etwa weil die Bibliothek kleiner wurde.
        if (result.page !== wanted) update({ page: result.page })
      },
      (problem: unknown) => {
        if (run !== generation.current || abort.signal.aborted) return
        setError(problem)
        setLoading(null)
      },
    )
    // Die naechste Anfrage zaehlt `generation` hoch, das macht diese hier ungueltig.
    return () => abort.abort()
  }, [kind, state, search, sort, tag, retries, update])

  async function loadMore() {
    if (!listing || loading !== null) return
    const run = generation.current
    const page = listing.page + 1
    setLoading('more')
    setMoreError(null)
    try {
      const result = await libraryApi.list({ kind, state, q: search, sort, page, tag }, aborter.current?.signal)
      if (run !== generation.current) return
      setListing((previous) => (previous ? append(previous, result.items, result.total, page) : previous))
      // Die Zahl der Seiten kommt in die Adresse. Das laedt nichts neu, es merkt sie nur fuers Zurueckkommen.
      update({ page })
    } catch (problem) {
      if (run === generation.current) setMoreError(problem)
    } finally {
      if (run === generation.current) setLoading(null)
    }
  }

  function chooseView(next: LibraryView) {
    setView(next)
    storeLibraryView(next)
  }

  const kindLabel = (value: MediaKind): string =>
    ({ movie: t('common.kind.movies'), series: t('common.kind.seriesPlural'), album: t('common.kind.musicPlural') })[value]

  const sortLabel = (value: LibrarySort): string => ({ title: t('library.sort.title'), year: t('library.sort.year'), added: t('library.sort.added') })[value]

  const viewLabel = (value: LibraryView): string => ({ grid: t('library.view.grid'), list: t('library.view.list') })[value]

  // Woertliche Schluessel, damit der Waechter sie sieht (keys.test.ts).
  const searchLabel = kind === 'series' ? t('library.filter.searchIn.series') : kind === 'album' ? t('library.filter.searchIn.album') : t('library.filter.searchIn.movie')
  const unfiltered = stateFilter === 'all' && search === '' && tag === ''
  // Eine ganz leere Bibliothek braucht keine Reihe aus Nullen ueber den beiden Wegen.
  const hasTitles = stats !== null && stats.movies + stats.series + stats.albums > 0

  return (
    <div className="flex flex-col gap-8">
      {/* Since the library-from-disk block "Hinzufuegen" is a small menu: search a movie, read the folders on disk, or (Musik M1) add an artist or album. */}
      <PageTitle sub={t('library.sub')}>{t('library.title')}</PageTitle>

      {stats && hasTitles && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <KeyFigure label={t('common.kind.movies')} value={formatNumber(stats.movies, language)} />
          {/* Eine Serie ist eine Zeile, die Folgen sind die Arbeit; bei Musik ebenso (Rueckmeldung 20.09.2026). */}
          <KeyFigure
            label={t('common.kind.seriesPlural')}
            value={formatNumber(stats.series, language)}
            second={{ label: t('library.figures.episodes'), value: formatNumber(stats.episodes ?? 0, language) }}
          />
          <KeyFigure
            label={t('library.figures.artists')}
            value={formatNumber(stats.artists ?? 0, language)}
            second={{ label: t('library.figures.albums'), value: formatNumber(stats.albums, language) }}
          />
          <KeyFigure label={t('library.figures.storage')} value={sizeText(t, stats.size_bytes, language)} hint={t('library.figures.storageHint')} />
        </div>
      )}
      {statsError !== null && <FormMessage>{errorText(t, statsError)}</FormMessage>}

      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <Segmented value={kind} options={KINDS} onChange={(next) => update({ kind: next, page: 1, ...(stateFiltersFor(next).includes(stateFilter) ? {} : { state: 'all' }) })} label={kindLabel} ariaLabel={t('library.filter.kind')} />
          {/* Eine Suche fuer alle drei Arten, in der Zeile des Umschalters. Sie sucht immer nur in der gewaehlten Art,
              und das steht im Feld (Rueckmeldung 20.09.2026). */}
          <div className="flex w-full flex-wrap items-center gap-3 lg:w-auto">
            <div className="relative min-w-0 flex-1 lg:w-80 lg:flex-none">
              <Symbol name="search" className="pointer-events-none absolute top-1/2 left-3.5 h-4 w-4 -translate-y-1/2 text-mist-600" />
              <input
                type="search"
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                placeholder={searchLabel}
                aria-label={searchLabel}
                className="w-full rounded-full border border-ink-700 bg-ink-900 py-2 pr-4 pl-10 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
              />
            </div>
            {/* Hinzufuegen steht bei der Suche, nicht bei der Ueberschrift, und nennt die gewaehlte Art. */}
            <AddMenu
              kind={addKind}
              onSearch={() => setAdding(movieOrSeries)}
              onAddArtist={() => setAddingArtist(true)}
              onAddAlbum={() => setAddingAlbum(true)}
            />
          </div>
        </div>
        {listed && selection.on && (
          <SelectionBar
            kind={kind}
            selection={selection}
            state={stateFilter}
            q={search}
            tag={tag}
            shown={(listing?.items ?? []).map((item) => item.id)}
            total={listing?.total ?? 0}
            onDone={() => setRetries((count) => count + 1)}
          />
        )}
        {/* Die Zustaende bekommen die ganze Breite; Ansicht, Sortierung und "Auswaehlen" stehen eine Zeile
            tiefer bei der Zahl der Titel (Rueckmeldung 20.09.2026). */}
        {listed && <StateChips kind={kind} value={stateFilter} stats={stats} onChange={(next) => update({ state: next, page: 1 })} />}
        {listed && (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
              {listing !== null && listing.kind === kind && t('library.count', { count: listing.total, value: formatNumber(listing.total, language) })}
              {loading === 'first' && <Spinner className="h-3.5 w-3.5" />}
            </p>
            <div className="flex shrink-0 flex-wrap items-center gap-3">
              {/* Rueckmeldung 20.09.2026: markieren, dann handeln. Die Leiste darueber zeigt, was gilt. */}
              {!selection.on && (
                <Button variant="ghost" size="sm" onClick={selection.start}>
                  <Symbol name="check" />
                  {t('library.select.open')}
                </Button>
              )}
              {kind === 'series' && (
                <Button variant="ghost" size="sm" onClick={() => setProposing(true)}>
                  <Symbol name="swap" />
                  {t('library.typeProposals.open')}
                </Button>
              )}
              <TagFilter value={tag} onChange={(next) => update({ tag: next, page: 1 })} />
              <Segmented value={view} options={LIBRARY_VIEWS} onChange={chooseView} label={viewLabel} ariaLabel={t('library.view.label')} />
              <label className="flex items-center gap-2 text-sm text-mist-500">
                {t('library.sort.label')}
                <select
                  value={sort}
                  onChange={(event) => update({ sort: event.target.value as LibrarySort, page: 1 })}
                  className="rounded-full border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
                >
                  {LIBRARY_SORTS.map((value) => (
                    <option key={value} value={value}>
                      {sortLabel(value)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>
        )}
      </div>

      {!listed ? (
        <MusicLibrary address={address} onChange={update} view={view} onChooseView={chooseView} stats={stats} />
      ) : error !== null ? (
        <div className="flex flex-col items-start gap-3">
          <FormMessage>{errorText(t, error)}</FormMessage>
          <Button variant="ghost" onClick={() => setRetries((count) => count + 1)}>
            <Symbol name="refresh" />
            {t('common.actions.retry')}
          </Button>
        </div>
      ) : listing === null || listing.kind !== kind ? (
        <p className="flex items-center justify-center gap-2 py-16 text-sm text-mist-500" role="status">
          <Spinner />
          {t('common.loading')}
        </p>
      ) : listing.total === 0 && unfiltered ? (
        <EmptyLibrary kind={movieOrSeries} onStart={() => setAdding(movieOrSeries)} />
      ) : listing.total === 0 ? (
        <p className="rounded-2xl border border-dashed border-ink-700 px-6 py-12 text-center text-sm text-mist-500" role="status">
          {t('library.empty')}
        </p>
      ) : (
        <section className="flex flex-col gap-4" aria-busy={loading === 'first'}>
          <div className={loading === 'first' ? 'opacity-60' : ''}>
            {view === 'list' ? (
              <TitleList titles={listing.items} selection={selection.on ? selection : null} />
            ) : (
              <TitleGrid titles={listing.items} selection={selection.on ? selection : null} />
            )}
          </div>
          {moreError !== null && <FormMessage>{errorText(t, moreError)}</FormMessage>}
          {listing.page * LIBRARY_PAGE_SIZE < listing.total && (
            <div className="flex flex-col items-center gap-2 pt-2">
              <Button variant="ghost" onClick={() => void loadMore()} loading={loading === 'more'} disabled={loading !== null}>
                {t('library.more')}
              </Button>
              <p className="text-xs text-mist-500 tabular-nums">
                {t('library.shown', { shown: formatNumber(listing.items.length, language), total: formatNumber(listing.total, language) })}
              </p>
            </div>
          )}
        </section>
      )}

      {adding !== null && <AddDialog kind={adding} onClose={() => setAdding(null)} />}
      {addingArtist && <AddArtistDialog onClose={() => setAddingArtist(false)} />}
      {proposing && (
        <SeriesTypeProposals
          onClose={() => setProposing(false)}
          onDone={(message) => {
            setProposing(false)
            notify(message)
            setRetries((count) => count + 1)
          }}
        />
      )}
      {addingAlbum && <AddAlbumDialog onClose={() => setAddingAlbum(false)} />}
    </div>
  )
}

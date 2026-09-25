import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError, errorText } from '../api/client'
import { FOLDER_READ_POLL_MAX, FOLDER_READ_POLL_MS, libraryApi } from '../api/library'
import { albumSearchesApi } from '../api/searches'
import type { SearchStartBody, SeriesBlock, TitleDetail, TitleVersion } from '../api/types'
import { PosterImage } from '../components/PosterImage'
import { Symbol } from '../components/Symbol'
import { Button, FormMessage, PageLoading, Section } from '../components/ui'
import { useNotice } from '../components/useNotice'
import { VersionChip } from '../components/VersionChip'
import { formatList, formatNumber } from '../lib/format'
import { genreText } from '../lib/names'
import { AlbumSearchSection } from './search/AlbumSearchSection'
import { SearchSection } from './search/SearchSection'
import { useEnabledIndexers } from './search/useEnabledIndexers'
import { useTitleSearch } from './search/useTitleSearch'
import { INDEXERS_TAB_PATH } from './settings/tabs'
import { AlbumHeader } from './title/AlbumHeader'
import { AlbumFolderCard } from './title/AlbumFolderCard'
import { AlbumVersionCard } from './title/AlbumVersionCard'
import { TagPreviewDialog } from './title/TagPreviewDialog'
import { AutomaticSearch } from './title/AutomaticSearch'
import { BlockedReleases } from './title/BlockedReleases'
import { ChangeVersionsDialog } from './title/ChangeVersionsDialog'
import { ChooseReleaseDialog } from './title/ChooseReleaseDialog'
import { HistoryList } from './title/HistoryList'
import { NumberingDialog } from './title/NumberingDialog'
import { ReleaseList } from './title/ReleaseList'
import { RenameTitleButton } from './rename/RenameTitleButton'
import { DeleteFilesDialog, type DeleteTarget } from './title/DeleteFilesDialog'
import { RatingBadges } from './title/RatingBadges'
import { TagEditor } from '../components/TagEditor'
import { tagsApi } from '../api/tags'
import { RemoveTitleDialog } from './title/RemoveTitleDialog'
import { SeasonList } from './title/SeasonList'
import { SeriesHints } from './title/SeriesHints'
import { definitionIdOf, regularSeasons, statusText, yearsText } from './title/seriesText'
import { TrackList } from './title/TrackList'
import { UNASSIGNED_ANCHOR, UnassignedFiles, UnassignedJump } from './title/UnassignedFiles'
import { useBlocklist } from './title/useBlocklist'
import { VersionCard } from './title/VersionCard'
import { WatchDialog } from './title/WatchDialog'

/** 404, oder eine Adresse, die gar keine Nummer eines Titels ist. */
function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || (error.status === 422 && error.code === 'invalid_input'))
}

function BackLink() {
  const { t } = useTranslation()
  return (
    <Link to="/" className="inline-flex w-fit items-center gap-2 text-sm text-mist-500 hover:text-mist-100">
      <Symbol name="back" />
      {t('title.backToLibrary')}
    </Link>
  )
}

/**
 * Ein Titel mit allen Fassungen nebeneinander. Das ist die Stelle, an der sich
 * nexcrate von zwei Radarr-Instanzen unterscheidet: 1080p und 4K sind ein Film.
 * Jede Fassung sagt, woher sie kommt; eigene lassen sich dazunehmen und entfernen.
 *
 * Seit Schritt 2c sucht ein Film auf Knopfdruck bei allen eingeschalteten Indexern. Das Ergebnis
 * steht direkt unter dem Kopf, je Fassung, und es wird nichts geladen.
 *
 * Seit S3 sucht auch eine Serie: oben die ganze Serie, dazu jede Staffel und jede Folge fuer sich. Das Ergebnis steht im
 * selben Suchabschnitt; ein Knopf in der Staffelliste springt dorthin.
 */
export function TitlePage() {
  const { id = '' } = useParams()
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const notify = useNotice()
  const [title, setTitle] = useState<TitleDetail | null>(null)
  const [monitorFor, setMonitorFor] = useState<number | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [retries, setRetries] = useState(0)
  const [dialog, setDialog] = useState<'versions' | 'remove' | 'numbering' | 'chooseRelease' | 'tags' | null>(null)
  // Seit S1: die Fassung am Titel, deren Regel gerade geaendert wird.
  const [watchFor, setWatchFor] = useState<number | null>(null)
  // Steigt, wenn sich Folgen geaendert haben. Offene Staffeln laden dann neu.
  const [episodesToken, setEpisodesToken] = useState(0)
  // /api/v1 V2: was "In den Papierkorb?" gerade fragt; null ohne offenen Dialog.
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null)
  const titleSearch = useTitleSearch(id)
  // Seit M3: die Suche nach einem Album, mit eigenen Routen.
  const albumSearch = useTitleSearch(id, albumSearchesApi)
  const enabledIndexers = useEnabledIndexers(title?.kind === 'movie' || title?.kind === 'series' || title?.kind === 'album')
  // Seit S3: Nach einem Suchstart aus der Staffelliste springt die Seite zum Suchabschnitt, sobald er da ist.
  const searchRef = useRef<HTMLDivElement>(null)
  const [jump, setJump] = useState(0)
  // Seit Schritt 3: Gesperrte Releases bei Filmen, seit der Durchsicht von S4 auch bei Serien, die nexcrate selbst laedt.
  const blocklist = useBlocklist(title !== null && (title.kind === 'movie' || title.kind === 'series') ? title.id : null)
  const noIndexerId = useId()
  // Seit S6: die Fassung, fuer die "Ordner neu einlesen" gerade laeuft, und wie viele Ordner der Titel gerade einliest.
  const [readingFor, setReadingFor] = useState<number | null>(null)
  const readingCount = title?.series?.reading?.length ?? 0

  useEffect(() => {
    let current = true
    const abort = new AbortController()
    setTitle(null)
    setError(null)
    libraryApi.detail(id, abort.signal).then(
      (result) => current && setTitle(result),
      (problem: unknown) => current && setError(problem),
    )
    return () => {
      current = false
      abort.abort()
    }
  }, [id, retries])

  useEffect(() => {
    if (jump === 0) return
    const target = searchRef.current
    if (!target) return
    if (typeof target.scrollIntoView === 'function') target.scrollIntoView({ behavior: 'smooth', block: 'start' })
    target.focus({ preventScroll: true })
  }, [jump])

  // Leise nachladen, ohne die Seite zu leeren: Ein offener Dialog bleibt mit seiner Meldung stehen.
  const refresh = useCallback(() => {
    libraryApi.detail(id).then(
      (result) => setTitle(result),
      () => undefined,
    )
  }, [id])

  // Eine Antwort, die Folgen geaendert haben kann: neue Regel, nachtraegliche Folgen, andere Fassungen.
  const replace = useCallback((detail: TitleDetail) => {
    setTitle(detail)
    setEpisodesToken((count) => count + 1)
  }, [])

  // Seit S6: Solange ein Serienordner eingelesen wird, fragt die Seite alle zwei Sekunden nach, hoechstens fuenf Minuten.
  useEffect(() => {
    if (readingCount === 0) return
    let asked = 0
    const timer = setInterval(() => {
      asked += 1
      if (asked > FOLDER_READ_POLL_MAX) {
        clearInterval(timer)
        return
      }
      refresh()
    }, FOLDER_READ_POLL_MS)
    return () => clearInterval(timer)
  }, [readingCount, refresh])

  // Rueckmeldung 20.09.2026: eine Filmfassung beobachten oder in Ruhe lassen.
  const changeMonitored = useCallback(
    async (version: TitleVersion, monitored: boolean) => {
      const definitionId = version.version_id
      if (typeof definitionId !== 'number' || title === null || monitorFor !== null) return
      setMonitorFor(definitionId)
      try {
        setTitle(await libraryApi.setMonitored(title.id, definitionId, monitored))
        notify(t(monitored ? 'title.version.watchedAgain' : 'title.version.leftAlone', { label: version.label }))
      } catch (problem) {
        notify(errorText(t, problem))
      } finally {
        setMonitorFor(null)
      }
    },
    [title, monitorFor, notify, t],
  )

  // "Ordner neu einlesen": Der Server antwortet mit dem Titel, in dem die Einlesung schon steht.
  const readFolder = useCallback(
    async (version: TitleVersion) => {
      const definitionId = version.version_id
      if (typeof definitionId !== 'number' || title === null || readingFor !== null) return
      setReadingFor(definitionId)
      try {
        setTitle(await libraryApi.readFolder(title.id, definitionId))
        notify(t('series.read.started'))
      } catch (problem) {
        notify(errorText(t, problem))
      } finally {
        setReadingFor(null)
      }
    },
    [title, readingFor, notify, t],
  )

  if (isNotFound(error)) {
    return (
      <div className="flex flex-col gap-6">
        <BackLink />
        <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-ink-700 px-6 py-14 text-center">
          <Symbol name="search" className="h-8 w-8 text-mist-600" />
          <h1 className="text-2xl font-bold tracking-tight">{t('title.notFoundTitle')}</h1>
          <p className="max-w-md text-sm text-mist-500">{t('title.notFound')}</p>
        </div>
      </div>
    )
  }

  if (error !== null) {
    return (
      <div className="flex flex-col items-start gap-4">
        <BackLink />
        <FormMessage>{errorText(t, error)}</FormMessage>
        <Button variant="ghost" onClick={() => setRetries((count) => count + 1)}>
          <Symbol name="refresh" />
          {t('common.actions.retry')}
        </Button>
      </div>
    )
  }

  if (!title) return <PageLoading />

  const isMovie = title.kind === 'movie'
  const isSeries = title.kind === 'series'
  const isAlbum = title.kind === 'album'
  const album = isAlbum ? (title.album ?? null) : null
  const albumVersion = title.versions[0] ?? null
  // Ein Server von vor S1 schickt fuer eine Serie keinen Block. Dann bleibt es beim Kopf und den Fassungen.
  const series: SeriesBlock | null = isSeries ? (title.series ?? null) : null
  const year = title.year !== null && title.year > 0 ? String(title.year) : null
  const seasonCount = series !== null ? regularSeasons(series.seasons).length : 0
  const meta = (
    isSeries
      ? [
          t('series.kind'),
          series !== null ? statusText(t, series.status) : null,
          (series !== null ? yearsText(t, series) : null) ?? year,
          seasonCount > 0 ? t('series.meta.seasons', { count: seasonCount, value: seasonCount }) : null,
          series !== null && series.networks.length > 0 ? t('series.networks', { names: formatList(series.networks, i18n.language) }) : null,
        ]
      : [
          t('common.kind.movie'),
          year,
          title.runtime_min ? t('common.units.minutes', { value: title.runtime_min }) : null,
          ...title.genres.map((genre) => genreText(t, genre)),
        ]
  ).filter(Boolean)
  const original = title.original_title && title.original_title !== title.title ? title.original_title : null
  const ids = [
    title.tmdb_id !== null ? t('title.ids.tmdb', { id: title.tmdb_id }) : null,
    series !== null && series.tvdb_id !== null ? t('series.ids.tvdb', { id: series.tvdb_id }) : null,
    title.imdb_id ? t('title.ids.imdb', { id: title.imdb_id }) : null,
  ].filter(Boolean)
  const watchVersion = watchFor !== null ? (title.versions.find((version) => version.id === watchFor) ?? null) : null
  // Nur wenn die Liste sicher kein eingeschaltetes Indexer zeigt. Weiss die Seite es nicht, entscheidet der Server.
  const noIndexer = (isMovie || isSeries || isAlbum) && enabledIndexers === 0
  const showSearch = (isMovie || isSeries) && (titleSearch.starting || titleSearch.search !== null || titleSearch.problem !== null)
  const showAlbumSearch = isAlbum && (albumSearch.starting || albumSearch.search !== null || albumSearch.problem !== null)
  const seriesSearchBlocked = noIndexer
  // Die Staffeln, die "Suchen" oben fragt: jede regulaere Staffel, in der eine Fassung eine ueberwachte, gelaufene Folge hat.
  const searchedSeasons = series !== null ? regularSeasons(series.seasons).filter((season) => season.versions.some((entry) => entry.aired_watched > 0)).length : 0
  // Ueberwacht keine Fassung etwas, fragt "Suchen" jede gelaufene Staffel; nehmen wuerde nexcrate dann nichts.
  const airedSeasons = series !== null ? regularSeasons(series.seasons).filter((season) => season.aired > 0).length : 0

  function searchSeries(scope: SearchStartBody) {
    void titleSearch.start(scope)
    setJump((count) => count + 1)
  }

  return (
    <div className="flex flex-col gap-8">
      <BackLink />

      {isAlbum && (
        <AlbumHeader
          title={title}
          versions={title.versions}
          onChangeVersions={() => setDialog('versions')}
          onRemove={() => setDialog('remove')}
          onSearch={() => void albumSearch.start()}
          searchBusy={albumSearch.busy}
          noIndexer={noIndexer}
          actions={<RenameTitleButton titleId={title.id} name={title.title} onRenamed={refresh} />}
        />
      )}

      {!isAlbum && (
      <header className="flex flex-col gap-6 sm:flex-row sm:items-end">
        <PosterImage url={title.poster_url} className="w-36 shrink-0 shadow-2xl shadow-black/40 sm:w-48" />
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <p className="text-sm text-mist-500">{meta.join(' · ')}</p>
          <h1 className="text-3xl font-bold tracking-tight wrap-anywhere sm:text-5xl">
            {title.title}
            <span className="text-accent-500">.</span>
          </h1>
          {original && <p className="text-sm text-mist-400">{t('title.originalTitle', { title: original })}</p>}
          <div className="flex flex-wrap gap-1.5">
            {title.versions.map((version) => (
              <VersionChip key={version.id} version={version} />
            ))}
          </div>
          <div className="flex flex-wrap gap-2 pt-1">
            {isMovie && (
              <Button
                onClick={() => void titleSearch.start()}
                loading={titleSearch.busy}
                disabled={noIndexer}
                aria-describedby={noIndexer ? noIndexerId : undefined}
              >
                {!titleSearch.busy && <Symbol name="search" />}
                {titleSearch.busy ? t('search.actionBusy') : t('search.action')}
              </Button>
            )}
            {isSeries && (
              <Button
                onClick={() => void titleSearch.start({ scope: 'series' })}
                loading={titleSearch.busy}
                disabled={seriesSearchBlocked}
                aria-describedby={noIndexer ? noIndexerId : undefined}
              >
                {!titleSearch.busy && <Symbol name="search" />}
                {titleSearch.busy ? t('search.actionBusy') : t('search.action')}
              </Button>
            )}
            <Button variant="ghost" onClick={() => setDialog('versions')}>
              <Symbol name="layers" />
              {t('title.actions.changeVersions')}
            </Button>
            <RenameTitleButton titleId={title.id} name={title.title} onRenamed={refresh} />
            {isSeries && (
              <Button variant="ghost" onClick={() => setDialog('numbering')}>
                <Symbol name="swap" />
                {t('series.numberingDialog.open')}
              </Button>
            )}
            <Button variant="ghost" onClick={() => setDialog('remove')}>
              <Symbol name="trash" />
              {t('title.actions.remove')}
            </Button>
          </div>
          {/* Seit S3: wie viele Staffeln "Suchen" fragt. */}
          {isSeries && (
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-mist-500">
              <Symbol name="info" className="h-4 w-4 shrink-0 text-info-500" />
              <span>
                {searchedSeasons > 0
                  ? t('search.series.seasons', { count: searchedSeasons, value: formatNumber(searchedSeasons, i18n.language) })
                  : airedSeasons > 0
                    ? t('search.series.seasonsNothingWatched', { count: airedSeasons, value: formatNumber(airedSeasons, i18n.language) })
                    : t('search.series.seasonsNotAired')}
              </span>
            </p>
          )}
          {noIndexer && (
            <p id={noIndexerId} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-mist-500">
              <Symbol name="info" className="h-4 w-4 shrink-0 text-info-500" />
              <span>{t('search.noIndexer')}</span>
              <Link to={INDEXERS_TAB_PATH} className="font-medium text-accent-400 hover:underline">
                {t('search.noIndexerLink')}
              </Link>
            </p>
          )}
        </div>
      </header>
      )}

      {(isMovie || isSeries) && <RatingBadges titleId={title.id} />}
      {/* Tags: ein Album zeigt die seines Kuenstlers, geaendert werden sie dort.
          ⚠️ Eigener Schluessel, nicht nur title.id: AutomaticSearch und SeasonList stehen im selben Block mit title.id,
          und gleiche Schluessel unter Geschwistern liessen bei jedem Neuladen ein altes Tag-Feld stehen (22.09.2026). */}
      {isAlbum ? (
        (title.tags ?? []).length > 0 && <TagEditor key={`tags-${title.id}`} tags={title.tags ?? []} readOnlyHint={t('tags.fromArtist')} />
      ) : (
        <TagEditor key={`tags-${title.id}`} tags={title.tags ?? []} onSave={(next) => tagsApi.setTitle(title.id, next).then((result) => result.tags)} />
      )}

      {(title.overview || ids.length > 0) && (
        <div className="flex max-w-3xl flex-col gap-2">
          {title.overview && <p className="text-sm leading-relaxed text-mist-300">{title.overview}</p>}
          {ids.length > 0 && <p className="text-xs text-mist-500">{ids.join(' · ')}</p>}
        </div>
      )}

      {series !== null && <SeriesHints title={title} series={series} onChanged={replace} onNumbering={() => setDialog('numbering')} />}
      {series !== null && <UnassignedJump files={series.unassigned_files} versions={title.versions} />}

      {showSearch && (
        <div ref={searchRef} tabIndex={-1} className="scroll-mt-4 outline-none">
          <SearchSection search={titleSearch.search} starting={titleSearch.starting} problem={titleSearch.problem} onLoaded={refresh} series={isSeries} />
        </div>
      )}

      {/* Seit Schritt 3c. Ein Server von davor schickt keinen Plan, dann gibt es das Panel nicht. `key`: Ein neuer Titel wartet auf keine alte Suche. */}
      {/* Seit Musik M5 auch fuer Alben, wie bei Filmen. */}
      {(isMovie || isAlbum) && title.search_plan && <AutomaticSearch key={title.id} titleId={title.id} titleYear={title.year} plan={title.search_plan} onRefresh={refresh} album={isAlbum} />}
      {/* Seit S5 auch fuer Serien, mit einer Zeile je Staffel. "Suchen" beim Paket oeffnet die Suche der Staffel mit Liste. */}
      {isSeries && title.search_plan && (
        <AutomaticSearch
          // Nicht nur die Nummer: SeasonList daneben traegt sie schon, doppelte Schluessel verdoppeln Kinder.
          key={`automatic-${title.id}`}
          titleId={title.id}
          titleYear={title.year}
          plan={title.search_plan}
          onRefresh={refresh}
          series
          onSearchSeason={seriesSearchBlocked ? undefined : (season) => searchSeries({ scope: 'season', season })}
          versions={title.versions}
          reading={series?.reading ?? []}
        />
      )}

      {showAlbumSearch && (
        <AlbumSearchSection
          search={albumSearch.search}
          starting={albumSearch.starting}
          problem={albumSearch.problem}
          busy={albumSearch.busy}
          onAliases={() => void albumSearch.start({ aliases: true })}
        />
      )}

      {isAlbum && album !== null && (
        <>
          <AlbumVersionCard
            titleId={title.id}
            album={album}
            version={albumVersion}
            onOpenChoose={() => setDialog('chooseRelease')}
            onLoaded={refresh}
            onSearchMissing={albumSearch.busy ? undefined : () => void albumSearch.start()}
          />
          <AlbumFolderCard titleId={title.id} album={album} onChanged={refresh} />
          <TrackList
            tracks={album.tracks}
            onDeleteAll={
              albumVersion !== null && albumVersion.source_name === null && album.tracks.some((track) => track.present) && definitionIdOf(albumVersion) !== null
                ? () => setDeleteTarget({ kind: 'album', versionId: definitionIdOf(albumVersion) as number, label: albumVersion.label })
                : undefined
            }
            onDeleteTrack={
              albumVersion !== null && albumVersion.source_name === null && definitionIdOf(albumVersion) !== null
                ? (track, fileId) =>
                    setDeleteTarget({ kind: 'track', versionId: definitionIdOf(albumVersion) as number, label: albumVersion.label, fileId, trackName: track.name })
                : undefined
            }
            onRetag={albumVersion !== null && albumVersion.source_name === null && album.actual !== null && album.tracks.some((track) => track.present) ? () => setDialog('tags') : undefined}
          />
          <ReleaseList releases={album.releases} />
        </>
      )}

      {!isAlbum && (
      <Section title={t('title.versions.title')} intro={t('title.versions.intro')}>
        {/* minmax(0,1fr): sonst drueckt ein ungebrochener Dateiname die Spalte am Telefon ueber den Rand. */}
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
          {title.versions.map((version) => (
            // Seit 3c: Ist die Automatik an, sagen eigene Fassungen, dass nexcrate von selbst sucht. Ohne Plan bleibt es beim Knopf.
            <VersionCard
              key={version.id}
              version={version}
              automatic={title.search_plan?.automatic === true}
              onChangeWatch={(chosen) => setWatchFor(chosen.id)}
              onChangeMonitored={isSeries ? undefined : (chosen, monitored) => void changeMonitored(chosen, monitored)}
              monitorBusy={monitorFor !== null && monitorFor === version.version_id}
              // Seit S6: nur eine Serie liest einen Ordner ein; bei einem Film gibt es den Knopf nicht.
              onReadFolder={isSeries ? (chosen) => void readFolder(chosen) : undefined}
              reading={(series?.reading ?? []).find((item) => item.version_id === version.version_id) ?? null}
              readBusy={readingFor !== null && readingFor === version.version_id}
              onDeleteFiles={(chosen) => {
                const versionId = definitionIdOf(chosen)
                if (versionId !== null) setDeleteTarget({ kind: isSeries ? 'series' : 'movie', versionId, label: chosen.label })
              }}
            />
          ))}
        </div>
      </Section>
      )}

      {series !== null && (
        <SeasonList
          key={title.id}
          title={title}
          series={series}
          reloadToken={episodesToken}
          onSwitched={refresh}
          onSearch={searchSeries}
          searchBlocked={seriesSearchBlocked}
          onFilesChanged={replace}
          onDeleteFiles={setDeleteTarget}
        />
      )}
      {series !== null && series.unassigned_files.length > 0 && (
        <div id={UNASSIGNED_ANCHOR} className="scroll-mt-4">
          <UnassignedFiles titleId={title.id} files={series.unassigned_files} versions={title.versions} onChanged={replace} onDeleteFiles={setDeleteTarget} />
        </div>
      )}

      <BlockedReleases entries={blocklist.entries} onRemoved={blocklist.drop} />

      <Section title={t('title.history.title')}>
        <HistoryList entries={title.history} />
      </Section>

      {dialog === 'versions' && (
        <ChangeVersionsDialog
          title={title}
          onClose={() => setDialog(null)}
          onStale={refresh}
          onChanged={(detail) => {
            replace(detail)
            setDialog(null)
          }}
        />
      )}
      {watchVersion !== null && series !== null && (
        <WatchDialog
          title={title}
          version={watchVersion}
          seasons={series.seasons}
          onClose={() => setWatchFor(null)}
          onStale={refresh}
          onChanged={(detail) => {
            replace(detail)
            setWatchFor(null)
            notify(t('series.watchDialog.saved', { label: watchVersion.label }))
          }}
        />
      )}
      {dialog === 'numbering' && isSeries && (
        <NumberingDialog
          title={title}
          onClose={() => setDialog(null)}
          onSaved={() => {
            setDialog(null)
            // Eine andere Episodengruppe aendert die Nummern der Folgen; offene Staffeln laden neu.
            setEpisodesToken((count) => count + 1)
            refresh()
            notify(t('series.numberingDialog.saved'))
          }}
        />
      )}
      {dialog === 'remove' && (
        <RemoveTitleDialog
          title={title}
          onClose={() => setDialog(null)}
          onStale={refresh}
          onChanged={(detail) => {
            setTitle(detail)
            setDialog(null)
            notify(t('title.remove.ownDone'))
          }}
          onRemoved={() => {
            notify(t('title.remove.done', { title: title.title }))
            navigate('/')
          }}
        />
      )}
      {deleteTarget !== null && (
        <DeleteFilesDialog
          titleId={title.id}
          target={deleteTarget}
          onClose={() => setDeleteTarget(null)}
          onDeleted={() => {
            setDeleteTarget(null)
            // Offene Staffeln laden ihre Folgen neu, die Seite ihre Zahlen.
            setEpisodesToken((count) => count + 1)
            refresh()
          }}
        />
      )}
      {dialog === 'tags' && isAlbum && <TagPreviewDialog titleId={title.id} onClose={() => setDialog(null)} onWritten={refresh} />}
      {dialog === 'chooseRelease' && album !== null && (
        <ChooseReleaseDialog titleId={title.id} album={album} onClose={() => setDialog(null)} onApplied={refresh} />
      )}
    </div>
  )
}

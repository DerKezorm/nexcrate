import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { tagsApi } from '../../api/tags'
import { TagEditor } from '../../components/TagEditor'

import { ApiError, errorText } from '../../api/client'
import { musicApi } from '../../api/music'
import type { ArtistDetail } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, PageLoading, Section, Switch } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { countryText } from '../../lib/country'
import { formatNumber } from '../../lib/format'
import { ArtistCover } from './ArtistCover'
import { ArtistGroups } from './ArtistGroups'
import { artistLoadingLine } from './loadingText'
import { AddAlbumDialog } from './AddAlbumDialog'
import { RemoveArtistDialog } from './RemoveArtistDialog'

/** 404, oder eine Adresse, die gar keine Nummer eines Kuenstlers ist. */
function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || (error.status === 422 && error.code === 'invalid_input'))
}

/** So oft fragt die Seite nach, solange der Kuenstler laedt (M1.2). */
const POLL_MS = 4000

function BackLink() {
  const { t } = useTranslation()
  return (
    <Link to="/" className="inline-flex w-fit items-center gap-2 text-sm text-mist-500 hover:text-mist-100">
      <Symbol name="back" />
      {t('title.backToLibrary')}
    </Link>
  )
}

/** Land und Zeitraum eines Kuenstlers in einer Zeile, so knapp wie die Kopfzeile eines Titels. */
function artistMeta(t: (key: string, values?: Record<string, unknown>) => string, artist: ArtistDetail, language: string): string {
  const years =
    artist.begin_year === null
      ? null
      : artist.end_year !== null
        ? t('music.artist.years.range', { from: artist.begin_year, to: artist.end_year })
        : t('music.artist.years.since', { year: artist.begin_year })
  return [artist.artist_type, countryText(artist.country, language), years].filter(Boolean).join(' · ')
}

/**
 * Die Kuenstlerseite (M1.5, Entscheidung 39): Kopf mit Name, Unterscheidung, Typ, Land, Jahren; die Regel fuer neue
 * Alben; der Ladezustand, solange er nicht `ready` ist; die Gruppen aus Entscheidung 26; "Jetzt bei MusicBrainz
 * aktualisieren" auf der Spur des Besitzers (Entscheidung 13). Solange geladen wird, fragt die Seite von selbst
 * nach, wie die Titelseite beim Einlesen eines Serienordners.
 */
export function ArtistPage() {
  const { id = '' } = useParams()
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const notify = useNotice()
  const [artist, setArtist] = useState<ArtistDetail | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [retries, setRetries] = useState(0)
  const [monitorBusy, setMonitorBusy] = useState(false)
  const [refreshBusy, setRefreshBusy] = useState(false)
  const [removing, setRemoving] = useState(false)
  const [addingAlbum, setAddingAlbum] = useState(false)

  useEffect(() => {
    let current = true
    const abort = new AbortController()
    setArtist(null)
    setError(null)
    musicApi.artist(Number(id), abort.signal).then(
      (result) => current && setArtist(result),
      (problem: unknown) => current && setError(problem),
    )
    return () => {
      current = false
      abort.abort()
    }
  }, [id, retries])

  const refresh = useCallback(() => {
    musicApi.artist(Number(id)).then(
      (result) => setArtist(result),
      () => undefined,
    )
  }, [id])

  // Solange geladen wird, fragt die Seite von selbst nach (M1.2), wie die Titelseite beim Einlesen eines Serienordners.
  useEffect(() => {
    if (artist === null || artist.load_state === 'ready' || artist.load_state === 'failed') return
    const timer = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(timer)
  }, [artist, refresh])

  async function changeMonitorNew(checked: boolean) {
    if (artist === null) return
    setMonitorBusy(true)
    try {
      setArtist((current) => (current === null ? current : { ...current, monitor_new: checked ? 'all' : 'none' }))
      const summary = await musicApi.changeArtist(artist.id, { monitor_new: checked ? 'all' : 'none' })
      setArtist((current) => (current === null ? current : { ...current, ...summary }))
    } catch (error) {
      refresh()
      notify(errorText(t, error))
    } finally {
      setMonitorBusy(false)
    }
  }

  async function changeMonitored(checked: boolean) {
    if (artist === null) return
    setMonitorBusy(true)
    try {
      setArtist((current) => (current === null ? current : { ...current, monitored: checked }))
      const summary = await musicApi.changeArtist(artist.id, { monitored: checked })
      setArtist((current) => (current === null ? current : { ...current, ...summary }))
    } catch (error) {
      refresh()
      notify(errorText(t, error))
    } finally {
      setMonitorBusy(false)
    }
  }

  async function refreshNow() {
    if (artist === null || refreshBusy) return
    setRefreshBusy(true)
    try {
      await musicApi.refreshArtist(artist.id)
      notify(t('music.artist.refreshed'))
      refresh()
    } catch (error) {
      notify(errorText(t, error))
    } finally {
      setRefreshBusy(false)
    }
  }

  if (isNotFound(error)) {
    return (
      <div className="flex flex-col gap-6">
        <BackLink />
        <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-ink-700 px-6 py-14 text-center">
          <Symbol name="search" className="h-8 w-8 text-mist-600" />
          <h1 className="text-2xl font-bold tracking-tight">{t('music.artist.notFoundTitle')}</h1>
          <p className="max-w-md text-sm text-mist-500">{t('music.artist.notFound')}</p>
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

  if (!artist) return <PageLoading />

  const loadingLine = artistLoadingLine(t, artist.load_state, artist.load_done, artist.load_total)
  const failed = artist.load_state === 'failed'

  return (
    <div className="flex flex-col gap-8">
      <BackLink />

      <header className="flex flex-col gap-6 sm:flex-row sm:items-end">
        <ArtistCover name={artist.name} url={artist.cover_url} className="w-36 shrink-0 shadow-2xl shadow-black/40 sm:w-48" />
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <p className="text-sm text-mist-500">{artistMeta(t, artist, i18n.language)}</p>
          <h1 className="text-3xl font-bold tracking-tight wrap-anywhere sm:text-5xl">
            {artist.name}
            <span className="text-accent-500">.</span>
          </h1>
          {artist.alias_display && <p className="text-sm text-mist-400">{artist.alias_display}</p>}
          {artist.disambiguation && <p className="text-sm text-mist-400">{artist.disambiguation}</p>}
          <p className="text-sm text-mist-300">
            {t('music.artist.counts', { count: artist.albums, value: formatNumber(artist.albums, i18n.language), complete: formatNumber(artist.complete, i18n.language) })}
          </p>
          <div className="flex flex-wrap gap-2 pt-1">
            <Button variant="ghost" onClick={() => setAddingAlbum(true)}>
              <Symbol name="note" />
              {t('music.addAlbum.title')}
            </Button>
            <Button variant="ghost" onClick={() => setRemoving(true)}>
              <Symbol name="trash" />
              {t('music.artist.actions.remove')}
            </Button>
          </div>
          {artist.mb_gone_at !== null && (
            <p className="flex items-start gap-2 text-sm text-mist-400">
              <Symbol name="info" className="mt-0.5 h-4 w-4 shrink-0 text-info-500" />
              {t('music.artist.gone')}
            </p>
          )}
        </div>
      </header>

      <Section title={t('music.artist.card.title')}>
        {!artist.is_various && (
          <TagEditor key={artist.id} tags={artist.tags ?? []} onSave={(next) => tagsApi.setArtist(artist.id, next).then((result) => result.tags)} />
        )}
        {!artist.is_various && (
          <Switch
            label={t('music.artist.monitored.label')}
            hint={artist.monitored === false ? t('music.artist.monitored.frozen') : t('music.artist.monitored.hint')}
            checked={artist.monitored !== false}
            onChange={(next) => void changeMonitored(next)}
            disabled={monitorBusy}
          />
        )}
        <Switch
          label={t('music.artist.monitorNew.label')}
          hint={t('music.artist.monitorNew.hint')}
          checked={artist.monitor_new === 'all'}
          onChange={(next) => void changeMonitorNew(next)}
          disabled={monitorBusy || artist.is_various}
        />
        {loadingLine !== null && (
          <p className="flex items-center gap-2 text-sm text-accent-400">
            <Symbol name="refresh" className="h-4 w-4 shrink-0" />
            {loadingLine}
          </p>
        )}
        {failed && (
          <FormMessage>{artist.load_error ? errorText(t, new ApiError(0, artist.load_error)) : t('music.artist.loadingFailed')}</FormMessage>
        )}
        {!artist.is_various && (
          <div>
            <Button variant="ghost" onClick={() => void refreshNow()} loading={refreshBusy}>
              <Symbol name="refresh" />
              {t('music.artist.refreshNow')}
            </Button>
          </div>
        )}
      </Section>

      <ArtistGroups
        groups={artist.groups}
        onWatch={async (albumId, monitored) => {
          await musicApi.setWatch(albumId, monitored)
          refresh()
        }}
      />

      {addingAlbum && <AddAlbumDialog artist={{ mbid: artist.mbid, name: artist.name }} onClose={() => setAddingAlbum(false)} />}
      {removing && (
        <RemoveArtistDialog
          artist={artist}
          onClose={() => setRemoving(false)}
          onRemoved={() => {
            notify(t('music.artist.remove.done', { name: artist.name }))
            navigate('/')
          }}
        />
      )}
    </div>
  )
}

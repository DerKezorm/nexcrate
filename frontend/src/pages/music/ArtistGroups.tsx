import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { errorText } from '../../api/client'
import type { ArtistAlbum, ArtistGroup } from '../../api/types'
import { PosterImage } from '../../components/PosterImage'
import { Symbol } from '../../components/Symbol'
import { FormMessage } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { STATE_LOOK } from '../../lib/states'
import { versionStateText } from '../../lib/stateText'
import { groupLabel } from './groupText'

type WatchHandler = (albumId: number, monitored: boolean) => Promise<void>

/**
 * Der Schalter "ueberwacht" einer Albumzeile (Entscheidung 39, E5): kompakt, ohne eigene Beschriftung, der Name des
 * Albums steht im aria-label. Ein Album ohne Fassung bekommt beim Einschalten seine Fassung.
 */
function WatchSwitch({ album, busy, onWatch }: { album: ArtistAlbum; busy: boolean; onWatch: WatchHandler }) {
  const { t } = useTranslation()
  const checked = album.monitored
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={t('music.artist.watchAlbum', { title: album.title })}
      disabled={busy}
      onClick={() => void onWatch(album.id, !checked)}
      className={
        'relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors disabled:cursor-not-allowed disabled:opacity-60 ' +
        (checked ? 'border-accent-500 bg-accent-500' : 'border-ink-600 bg-ink-700')
      }
    >
      <span aria-hidden="true" className={'inline-block h-4 w-4 rounded-full transition-transform ' + (checked ? 'translate-x-6 bg-ink-900' : 'translate-x-1 bg-mist-500')} />
    </button>
  )
}

/** Eine Albumzeile einer Gruppe (M1.5.39): Cover, Titel, Jahr, Titelzahl der Zielausgabe, Zustand, Schalter. Klick oeffnet die Albumseite. */
function AlbumRow({ album, busy, onWatch }: { album: ArtistAlbum; busy: boolean; onWatch: WatchHandler | null }) {
  const { t, i18n } = useTranslation()
  const look = album.state !== null ? STATE_LOOK[album.state] : null
  return (
    <li className="flex items-center gap-3 px-4 py-2.5 hover:bg-ink-850">
      <Link to={`/titel/${album.id}`} className="group flex min-w-0 flex-1 items-center gap-3">
        <PosterImage url={album.cover_url} placeholder="note" square className="w-11 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-mist-100 group-hover:text-accent-400">{album.title}</p>
          <p className="truncate text-xs text-mist-500">
            {[
              album.year !== null && album.year > 0 ? String(album.year) : null,
              album.target_tracks !== null ? t('music.artist.trackCount', { count: album.target_tracks, value: formatNumber(album.target_tracks, i18n.language) }) : null,
            ]
              .filter(Boolean)
              .join(' · ')}
          </p>
        </div>
      </Link>
      {look !== null && album.state !== null && (
        <span className={'inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold whitespace-nowrap ' + look.chip}>
          <Symbol name={look.symbol} className="h-3.5 w-3.5" />
          {versionStateText(t, album.state)}
        </span>
      )}
      {onWatch !== null && <WatchSwitch album={album} busy={busy} onWatch={onWatch} />}
    </li>
  )
}

/** Eine Gruppe der Kuenstlerseite: aufklappbar, mit Zahl (Entscheidung 26, 39). */
function GroupBlock({ group, initialOpen, busyId, onWatch }: { group: ArtistGroup; initialOpen: boolean; busyId: number | null; onWatch: WatchHandler | null }) {
  const { t, i18n } = useTranslation()
  const [open, setOpen] = useState(initialOpen)
  return (
    <div className="overflow-hidden rounded-2xl border border-ink-700 bg-ink-850/50">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-ink-850"
      >
        <h3 className="text-sm font-semibold text-mist-100">{groupLabel(t, group.group)}</h3>
        <span className="flex items-center gap-2.5">
          <span className="text-sm text-mist-500 tabular-nums">{formatNumber(group.albums.length, i18n.language)}</span>
          <Symbol name="chevronDown" className={'h-4 w-4 text-mist-500 transition-transform ' + (open ? '' : '-rotate-90')} />
        </span>
      </button>
      {open && (
        <ul className="border-t border-ink-700">
          {group.albums.map((album) => (
            <AlbumRow key={album.id} album={album} busy={busyId === album.id} onWatch={onWatch} />
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * Die Gruppen einer Kuenstlerseite (Entscheidung 26): Studioalben und EPs offen, die uebrigen zugeklappt mit Zahl.
 * Leere Gruppen bleiben weg. `key={group.group}` an `GroupBlock` sorgt dafuer, dass ein neu geladener Stand den
 * Aufklappzustand des Besitzers nicht ueberschreibt, solange die Gruppe dieselbe bleibt.
 */
export function ArtistGroups({ groups, onWatch = null }: { groups: ArtistGroup[]; onWatch?: WatchHandler | null }) {
  const { t } = useTranslation()
  const [busyId, setBusyId] = useState<number | null>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const nonEmpty = groups.filter((group) => group.albums.length > 0)
  const handleWatch: WatchHandler | null =
    onWatch === null
      ? null
      : async (albumId, monitored) => {
          setBusyId(albumId)
          setProblem(null)
          try {
            await onWatch(albumId, monitored)
          } catch (error: unknown) {
            setProblem(error)
          } finally {
            setBusyId(null)
          }
        }
  return (
    <div className="flex flex-col gap-3">
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      {nonEmpty.map((group) => (
        <GroupBlock key={group.group} group={group} initialOpen={group.open} busyId={busyId} onWatch={handleWatch} />
      ))}
    </div>
  )
}

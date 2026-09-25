import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { AlbumTrack } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button, Section } from '../../components/ui'
import { formatDateTime } from '../../lib/format'
import { sizeText } from '../../lib/size'
import { tagStateText, viaText } from './tagText'

function trackTime(lengthMs: number | null): string | null {
  if (lengthMs === null || lengthMs <= 0) return null
  const totalSeconds = Math.round(lengthMs / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

/** Was ueber die Datei eines Titels bekannt ist: Name, Qualitaet, Groesse, Tags und woher sie kam. */
function TrackFileDetails({ track }: { track: AlbumTrack }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const file = track.file
  if (!track.present || file === null) return <p className="text-sm text-mist-500">{t('title.album.tracks.noFile')}</p>
  const via = viaText(t, file.via)
  const tags = tagStateText(t, file.tags_state)
  const rows: [string, string][] = []
  if (file.relative_path) rows.push([t('title.album.tracks.file'), file.relative_path])
  const facts = [file.quality, typeof file.size === 'number' && file.size > 0 ? sizeText(t, file.size, language) : null].filter((value): value is string => Boolean(value))
  if (facts.length > 0) rows.push([t('title.album.tracks.quality'), facts.join(' · ')])
  if (tags !== null) rows.push([t('title.album.tracks.tags'), tags])
  if (file.source === 'download') {
    rows.push([t('title.album.tracks.fromDownload'), file.download_release ?? '?'])
    if (file.download_path) rows.push([t('title.album.tracks.nameInDownload'), file.download_path])
    if (via !== null) rows.push([t('title.album.tracks.matchedBy'), via])
    if (file.filed_at) rows.push([t('title.album.tracks.filedAt'), formatDateTime(file.filed_at, language)])
  } else if (file.source === 'lidarr') {
    rows.push([t('title.album.tracks.origin'), t('title.album.tracks.fromLidarr')])
  }
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-mist-500">{label}</dt>
          <dd className="break-words text-mist-200">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

/**
 * Die Titelliste der Zielausgabe, je Medium (Entscheidung 40): Nummer, Name, Laenge, "vorhanden" oder "fehlt". Seit M4
 * je Datei, woher ihre Tags stammen, und bei einem eigenen Album mit Dateien "Tags prüfen" (Entscheidungen 29 und 30).
 * Seit dem 19.09.2026 (Wunsch des Besitzers) klappt ein Klick auf den Titel die Datei auf: Name, Qualitaet, aus
 * welchem Download unter welchem Namen und wie sie zugeordnet wurde.
 */
export function TrackList({
  tracks,
  onRetag,
  onDeleteAll,
  onDeleteTrack,
}: {
  tracks: AlbumTrack[]
  onRetag?: () => void
  /** Seit V5: alle Dateien des Albums in den Papierkorb. Ohne: kein Knopf. */
  onDeleteAll?: () => void
  /** Die Datei eines Titels in den Papierkorb. */
  onDeleteTrack?: (track: AlbumTrack, fileId: number) => void
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState<number | null>(null)
  if (tracks.length === 0) return null
  const mediaCount = new Set(tracks.map((track) => track.medium)).size
  const byMedium = new Map<number, AlbumTrack[]>()
  for (const track of tracks) {
    const list = byMedium.get(track.medium) ?? []
    list.push(track)
    byMedium.set(track.medium, list)
  }

  return (
    <Section
      title={t('title.album.tracks.title')}
      actions={
        onRetag || onDeleteAll ? (
          <div className="flex flex-wrap gap-2">
            {onRetag && (
              <Button variant="ghost" size="sm" onClick={onRetag}>
                <Symbol name="note" />
                {t('title.album.tags.open')}
              </Button>
            )}
            {onDeleteAll && (
              <Button variant="ghost" size="sm" onClick={onDeleteAll}>
                <Symbol name="trash" />
                {t('title.album.tracks.deleteAll')}
              </Button>
            )}
          </div>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-4">
        {[...byMedium.entries()].map(([medium, items]) => (
          <div key={medium} className="flex flex-col gap-1">
            {mediaCount > 1 && (
              <h3 className="text-sm font-semibold text-mist-400">
                {items[0].medium_format ? t('title.album.tracks.mediumNamed', { number: medium, format: items[0].medium_format }) : t('title.album.tracks.medium', { number: medium })}
              </h3>
            )}
            <ul className="overflow-hidden rounded-xl border border-ink-700 bg-ink-850/50">
              {items.map((track) => {
                const time = trackTime(track.length_ms)
                const expanded = open === track.id
                const detailsId = `track-file-${track.id}`
                return (
                  <li key={track.id} className="border-b border-ink-700/60 last:border-b-0">
                    <button
                      type="button"
                      aria-expanded={expanded}
                      aria-controls={detailsId}
                      onClick={() => setOpen(expanded ? null : track.id)}
                      className="flex w-full items-center gap-3 px-4 py-2 text-left text-sm hover:bg-ink-800/60"
                    >
                      <span className="w-8 shrink-0 text-mist-600 tabular-nums">{track.number ?? track.position}</span>
                      <span className="min-w-0 flex-1 truncate text-mist-200">{track.name}</span>
                      {time !== null && <span className="shrink-0 text-mist-500 tabular-nums">{time}</span>}
                      {track.present && tagStateText(t, track.file?.tags_state) !== null && (
                        <span className="hidden shrink-0 text-xs text-mist-500 sm:inline">{tagStateText(t, track.file?.tags_state)}</span>
                      )}
                      {track.present ? (
                        <span className="inline-flex shrink-0 items-center gap-1 text-ok-500">
                          <Symbol name="check" className="h-3.5 w-3.5" />
                          {t('title.album.tracks.present')}
                        </span>
                      ) : (
                        <span className="inline-flex shrink-0 items-center gap-1 text-mist-500">
                          <Symbol name="clock" className="h-3.5 w-3.5" />
                          {t('title.album.tracks.missing')}
                        </span>
                      )}
                      <Symbol name={expanded ? 'chevronDown' : 'chevron'} className="h-3.5 w-3.5 shrink-0 text-mist-600" />
                    </button>
                    {expanded && (
                      <div id={detailsId} className="border-t border-ink-700/60 bg-ink-900/60 px-4 py-3 pl-15">
                        <TrackFileDetails track={track} />
                        {onDeleteTrack && track.present && typeof track.file?.id === 'number' && (
                          <Button variant="ghost" size="sm" className="mt-2" onClick={() => onDeleteTrack(track, track.file?.id as number)}>
                            <Symbol name="trash" />
                            {t('title.album.tracks.deleteOne')}
                          </Button>
                        )}
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </div>
    </Section>
  )
}

import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { musicApi, type FolderRead } from '../../api/music'
import type { AlbumBlock, AlbumTrack, AlbumUnclearFile } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, SelectField } from '../../components/ui'
import { formatDateTime, formatNumber } from '../../lib/format'
import { albumQualityText } from '../../lib/musicSteps'
import { sizeText } from '../../lib/size'
import { proposalFor } from './albumFolderProposal'

const NONE = 'none'

function trackLabel(track: AlbumTrack): string {
  return `${track.medium}-${String(track.number ?? track.position).padStart(2, '0')} ${track.name}`
}

/** Eine unklare Datei: Titel waehlen (Vorschlag zuerst) oder keinem Titel. */
function UnclearRow({ titleId, file, free, onSettled }: { titleId: number; file: AlbumUnclearFile; free: AlbumTrack[]; onSettled: () => void }) {
  const { t, i18n } = useTranslation()
  const proposal = proposalFor(file, free)
  const ordered = proposal === null ? free : [proposal, ...free.filter((track) => track.id !== proposal.id)]
  const [choice, setChoice] = useState<string>(proposal !== null ? String(proposal.id) : NONE)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function settle(trackId: number | null) {
    setBusy(true)
    setProblem(null)
    try {
      await musicApi.settleFile(titleId, file.id, trackId)
      onSettled()
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  const details = [file.quality ? albumQualityText(t, file.quality) : null, file.size ? sizeText(t, file.size, i18n.language) : null].filter(Boolean).join(' · ')
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-xl border border-ink-700 bg-ink-950/60 p-3">
      <p className="font-mono text-xs break-all text-mist-200">{file.relative_path}</p>
      {details && <p className="text-xs text-mist-500">{details}</p>}
      {free.length > 0 ? (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <div className="min-w-0 flex-1">
            <SelectField label={t('title.album.folder.track')} value={choice} disabled={busy} onChange={(event) => setChoice(event.target.value)}>
              <option value={NONE}>{t('title.album.folder.pick')}</option>
              {ordered.map((track) => (
                <option key={track.id} value={track.id}>
                  {track.id === proposal?.id ? t('title.album.folder.proposal', { track: trackLabel(track) }) : trackLabel(track)}
                </option>
              ))}
            </SelectField>
          </div>
          <Button size="sm" disabled={busy || choice === NONE} onClick={() => void settle(Number(choice))}>
            <Symbol name="check" />
            {t('title.album.folder.assign')}
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => void settle(null)}>
            {t('title.album.folder.noTrack')}
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm text-mist-400">{t('title.album.folder.noFreeTrack')}</p>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => void settle(null)}>
            {t('title.album.folder.noTrack')}
          </Button>
        </div>
      )}
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
    </li>
  )
}

/**
 * Musik M6 (Entscheidungen 12 und 15): der Albumordner eines eigenen Albums. Dateien ohne Titel
 * ("unklar") ordnet der Besitzer hier zu oder laesst sie ohne Titel liegen; "Ordner neu einlesen" nimmt neue Dateien
 * auf und vergisst verschwundene. Dateien werden nie verschoben.
 */
export function AlbumFolderCard({ titleId, album, onChanged }: { titleId: number; album: AlbumBlock; onChanged: () => void }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const unclear = album.unclear_files ?? []
  const free = album.tracks.filter((track) => !track.present)
  const [reading, setReading] = useState(false)
  const [read, setRead] = useState<FolderRead | null>(null)
  const [problem, setProblem] = useState<unknown>(null)

  if (!album.can_read_folder && unclear.length === 0) return null

  async function readAgain() {
    setReading(true)
    setProblem(null)
    setRead(null)
    try {
      setRead(await musicApi.readFolder(titleId))
      onChanged()
    } catch (error) {
      setProblem(error)
    } finally {
      setReading(false)
    }
  }

  return (
    <div className="flex flex-col gap-4 rounded-2xl border border-ink-700 bg-ink-900/60 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">{t('title.album.folder.title')}</h2>
        {album.can_read_folder && (
          <Button size="sm" variant="ghost" loading={reading} onClick={() => void readAgain()}>
            <Symbol name="refresh" />
            {t('title.album.folder.read')}
          </Button>
        )}
      </div>
      {album.files_read_at && <p className="text-xs text-mist-500">{t('title.album.folder.readAt', { time: formatDateTime(album.files_read_at, language) })}</p>}
      {read !== null && (
        <FormMessage tone="ok" role="status">
          {t('title.album.folder.readResult', {
            linked: formatNumber(read.linked, language),
            unclear: formatNumber(read.unclear, language),
            gone: formatNumber(read.gone, language),
          })}
        </FormMessage>
      )}
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      {unclear.length > 0 ? (
        <div className="flex flex-col gap-2">
          <p className="flex items-start gap-2 text-sm text-bad-500">
            <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              {t('title.album.folder.unclear', { count: unclear.length, value: formatNumber(unclear.length, language) })}{' '}
              <span className="text-mist-400">{t('title.album.folder.unclearHint')}</span>
            </span>
          </p>
          <ul className="flex flex-col gap-2" aria-label={t('title.album.folder.unclearList')}>
            {unclear.map((file) => (
              <UnclearRow key={file.id} titleId={titleId} file={file} free={free} onSettled={onChanged} />
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-sm text-mist-400">{t('title.album.folder.allClear')}</p>
      )}
    </div>
  )
}

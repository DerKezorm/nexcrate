import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { downloadsApi } from '../../api/downloads'
import type { AlbumAudioFile, AlbumFiles, AlbumTrackChoice, Download } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, SelectField, Spinner } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { albumQualityText } from '../../lib/musicSteps'
import { sizeText } from '../../lib/size'
import { type AlbumChoice, doubleTracks, durationText, EDITABLE, initialAlbumChoices, LOOSE, trackCode } from './albumAssignChoices'
import { FinishDialog } from './FinishDialog'

/**
 * "Von Hand zuordnen" fuer einen Albendownload (M4, Entscheidungen 31 bis 33): oben die Ausgabe, darunter je Datei mit
 * Laenge, Format und Tags ein Titel dieser Ausgabe, "passt zu keinem Titel, trotzdem ablegen" oder "nicht ablegen", und
 * die Titelliste mit dem, was liegt, was gewaehlt ist und was fehlt. Die Wahl gilt nur fuer diesen Download.
 * Bei "nicht besser" sagt der Dialog, was schlechter wuerde, und ersetzt erst auf ausdrueckliche Bestaetigung.
 */
export function AlbumAssignDialog({ download, onClose, onDone }: { download: Download; onClose: () => void; onDone: (message: string) => void }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [releaseId, setReleaseId] = useState<number | null>(null)
  const [data, setData] = useState<AlbumFiles | null>(null)
  const [loadProblem, setLoadProblem] = useState<unknown>(null)
  const [choices, setChoices] = useState<Record<number, AlbumChoice>>({})
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const [finishing, setFinishing] = useState(false)
  const notBetter = download.problem?.code === 'album_not_better'

  useEffect(() => {
    const abort = new AbortController()
    downloadsApi.albumFiles(download.id, releaseId, abort.signal).then(
      (result) => {
        if (abort.signal.aborted) return
        setData(result)
        setChoices(initialAlbumChoices(result))
      },
      (error: unknown) => {
        if (!abort.signal.aborted) setLoadProblem(error)
      },
    )
    return () => abort.abort()
  }, [download.id, releaseId])

  const severalMedia = useMemo(() => new Set((data?.tracks ?? []).map((track) => track.medium)).size > 1, [data])
  const trackById = useMemo(() => new Map((data?.tracks ?? []).map((track) => [track.id, track])), [data])
  const editable = (data?.files ?? []).filter((file) => !file.placed && EDITABLE.includes(file.decision))
  const placed = (data?.files ?? []).filter((file) => file.placed)
  const doubles = data !== null ? doubleTracks(data, choices) : new Set<number>()
  const chosen = new Set(Object.values(choices).flatMap((choice) => (choice.kind === 'track' ? [choice.trackId] : [])))
  const toFile = Object.values(choices).filter((choice) => choice.kind !== 'none').length
  const missing = (data?.tracks ?? []).filter((track) => track.held === null && !chosen.has(track.id))

  function choose(key: number, value: string) {
    const next: AlbumChoice = value === '' ? { kind: 'none' } : value === LOOSE ? { kind: 'loose' } : { kind: 'track', trackId: Number(value) }
    setChoices((current) => ({ ...current, [key]: next }))
  }

  function takeProposals() {
    if (data === null) return
    setChoices((current) => {
      const next = { ...current }
      const taken = new Set([...data.files.filter((file) => file.placed && file.track_id !== null).map((file) => file.track_id as number)])
      for (const choice of Object.values(next)) if (choice.kind === 'track') taken.add(choice.trackId)
      for (const file of editable) {
        if (next[file.key]?.kind !== 'none') continue
        const proposal = file.proposal.find((id) => trackById.has(id) && !taken.has(id) && trackById.get(id)?.held === null)
        if (proposal !== undefined) {
          next[file.key] = { kind: 'track', trackId: proposal }
          taken.add(proposal)
        }
      }
      return next
    })
  }

  async function submit() {
    if (data === null) return
    setBusy(true)
    setProblem(null)
    try {
      const files = editable.map((file) => {
        const choice = choices[file.key] ?? { kind: 'none' }
        return { key: file.key, track_id: choice.kind === 'track' ? choice.trackId : null, loose: choice.kind === 'loose' }
      })
      await downloadsApi.albumAssign(download.id, {
        release_id: data.release_fixed ? null : data.release_id,
        files,
        confirm: notBetter ? ['not_better'] : [],
      })
      onDone(t('downloads.albumAssign.done'))
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  function close() {
    if (!busy) onClose()
  }

  const values = download.problem?.values ?? {}
  const before = typeof values.step_before === 'string' ? albumQualityText(t, values.step_before) : '?'
  const after = typeof values.step_after === 'string' ? albumQualityText(t, values.step_after) : '?'

  return (
    <Dialog
      open
      wide
      title={t('downloads.albumAssign.title', { album: download.title.title })}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="ghost" onClick={() => setFinishing(true)} disabled={busy || data === null}>
            {t('downloads.assign.finish')}
          </Button>
          <Button variant={notBetter ? 'danger' : 'primary'} onClick={() => void submit()} loading={busy} disabled={data === null || toFile === 0 || doubles.size > 0}>
            {notBetter ? t('downloads.albumAssign.replace') : t('downloads.albumAssign.submit', { count: toFile, value: formatNumber(toFile, language) })}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-mist-400">
          {download.title.artist ? t('downloads.albumAssign.sub', { artist: download.title.artist, version: download.version.label }) : download.version.label}
        </p>
        {notBetter && (
          <p className="rounded-xl border border-bad-500/40 bg-bad-500/10 p-3 text-sm text-mist-200">
            {t('downloads.albumAssign.notBetter', {
              before,
              after,
              tracksBefore: formatNumber(Number(values.tracks_before ?? 0), language),
              tracksAfter: formatNumber(Number(values.tracks_after ?? 0), language),
            })}
          </p>
        )}
        {loadProblem !== null && <FormMessage>{errorText(t, loadProblem)}</FormMessage>}
        {data === null && loadProblem === null && (
          <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
            <Spinner />
            {t('downloads.assign.loading')}
          </p>
        )}
        {data !== null && (
          <>
            <SelectField
              label={t('downloads.albumAssign.release')}
              hint={data.release_fixed ? t('downloads.albumAssign.releaseFixed') : t('downloads.albumAssign.releaseHint')}
              value={data.release_id === null ? '' : String(data.release_id)}
              disabled={data.release_fixed || busy}
              onChange={(event) => setReleaseId(event.target.value === '' ? null : Number(event.target.value))}
            >
              {data.releases.map((release) => (
                <option key={release.id} value={release.id}>
                  {[
                    release.name,
                    release.disambiguation,
                    release.formats.join(' + ') || null,
                    t('downloads.albumAssign.trackCount', { count: release.track_count, value: formatNumber(release.track_count, language) }),
                    release.country,
                    release.date,
                    release.id === data.target_release_id ? t('downloads.albumAssign.target') : null,
                    release.id === data.read_release_id ? t('downloads.albumAssign.read') : null,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </option>
              ))}
            </SelectField>
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-sm text-mist-200" role="status">
                {missing.length > 0
                  ? t('downloads.albumAssign.missing', { count: missing.length, value: formatNumber(missing.length, language) })
                  : t('downloads.albumAssign.complete')}
              </p>
              {editable.some((file) => file.proposal.length > 0) && (
                <Button variant="ghost" size="sm" onClick={takeProposals}>
                  <Symbol name="sparkle" />
                  {t('downloads.albumAssign.useProposals')}
                </Button>
              )}
            </div>
            <ul className="flex flex-col gap-3" aria-label={t('downloads.albumAssign.filesTitle')}>
              {editable.map((file) => (
                <FileRow
                  key={file.key}
                  file={file}
                  tracks={data.tracks}
                  severalMedia={severalMedia}
                  choice={choices[file.key] ?? { kind: 'none' }}
                  doubles={doubles}
                  onChoose={(value) => choose(file.key, value)}
                  trackById={trackById}
                />
              ))}
              {placed.map((file) => (
                <li key={file.key} className="flex min-w-0 flex-col gap-1 rounded-xl border border-ink-700/60 p-3 text-mist-500">
                  <p className="font-mono text-xs break-all">{file.path}</p>
                  <p className="text-xs">
                    {file.track_id !== null && trackById.has(file.track_id)
                      ? t('downloads.albumAssign.placedAs', { code: trackCode(trackById.get(file.track_id) as AlbumTrackChoice, severalMedia) })
                      : t('downloads.albumAssign.placedLoose')}
                  </p>
                </li>
              ))}
            </ul>
            <section aria-label={t('downloads.albumAssign.tracksTitle')} className="flex flex-col gap-2 border-t border-ink-700 pt-4">
              <h3 className="text-sm font-semibold text-mist-200">{t('downloads.albumAssign.tracksTitle')}</h3>
              <ol className="flex flex-col gap-1">
                {data.tracks.map((track) => {
                  const state = track.held !== null ? 'held' : chosen.has(track.id) ? 'chosen' : 'missing'
                  return (
                    <li key={track.id} className="flex min-w-0 items-baseline gap-2 text-sm">
                      <span className="w-12 shrink-0 font-mono text-xs text-mist-500 tabular-nums">{trackCode(track, severalMedia)}</span>
                      <span className="min-w-0 flex-1 wrap-anywhere text-mist-100">{track.name}</span>
                      <span className="shrink-0 text-xs text-mist-500 tabular-nums">{durationText(track.length_ms)}</span>
                      <Badge tone={state === 'held' ? 'ok' : state === 'chosen' ? 'accent' : 'bad'}>
                        {state === 'held' ? t('downloads.albumAssign.held') : state === 'chosen' ? t('downloads.albumAssign.chosen') : t('downloads.albumAssign.gap')}
                      </Badge>
                    </li>
                  )
                })}
              </ol>
            </section>
            <p className="text-xs text-mist-500">{t('downloads.albumAssign.onlyThis')}</p>
          </>
        )}
        {doubles.size > 0 && (
          <FormMessage>
            {t('downloads.albumAssign.twice', {
              codes: [...doubles].map((id) => (trackById.has(id) ? trackCode(trackById.get(id) as AlbumTrackChoice, severalMedia) : String(id))).join(', '),
            })}
          </FormMessage>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
      {finishing && (
        <FinishDialog
          download={download}
          onClose={() => setFinishing(false)}
          onDone={(message) => {
            setFinishing(false)
            onDone(message)
          }}
        />
      )}
    </Dialog>
  )
}

function FileRow({
  file,
  tracks,
  severalMedia,
  choice,
  doubles,
  onChoose,
  trackById,
}: {
  file: AlbumAudioFile
  tracks: AlbumTrackChoice[]
  severalMedia: boolean
  choice: AlbumChoice
  doubles: Set<number>
  onChoose: (value: string) => void
  trackById: Map<number, AlbumTrackChoice>
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const value = choice.kind === 'track' ? String(choice.trackId) : choice.kind === 'loose' ? LOOSE : ''
  const proposal = file.proposal.length > 0 ? trackById.get(file.proposal[0]) : undefined
  const facts = [
    sizeText(t, file.size_bytes, language),
    durationText(file.duration_ms),
    file.codec,
    file.bit_depth !== null && file.bit_depth >= 24 ? t('downloads.albumAssign.bits', { bits: file.bit_depth }) : null,
    file.bitrate !== null && file.codec !== null && !['FLAC', 'ALAC', 'WAV'].includes(file.codec) ? t('downloads.albumAssign.kbit', { value: file.bitrate }) : null,
  ].filter((fact): fact is string => fact !== null && fact !== '')
  const tagged = [file.tags.tracknumber ? (file.tags.discnumber ? `${file.tags.discnumber}-${file.tags.tracknumber}` : file.tags.tracknumber) : null, file.tags.title]
    .filter(Boolean)
    .join(' ')
  return (
    <li className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col gap-1">
        <p className="font-mono text-xs break-all text-mist-100">{file.path}</p>
        <p className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-mist-500 tabular-nums">{facts.join(' · ')}</p>
        <p className="text-xs text-mist-400">{tagged !== '' ? t('downloads.albumAssign.tags', { tags: tagged }) : t('downloads.albumAssign.noTags')}</p>
        {file.other_album !== null && <p className="text-xs text-mist-400">{t('downloads.albumAssign.otherAlbum', { album: file.other_album.title })}</p>}
      </div>
      <div className="flex min-w-0 flex-col gap-1">
        <select
          aria-label={t('downloads.albumAssign.track') + ': ' + file.path}
          value={value}
          onChange={(event) => onChoose(event.target.value)}
          className={
            'min-w-0 rounded-xl border bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none ' +
            (choice.kind === 'track' && doubles.has(choice.trackId) ? 'border-bad-500' : 'border-ink-700')
          }
        >
          <option value="">{t('downloads.assign.notFiled')}</option>
          <option value={LOOSE}>{t('downloads.albumAssign.loose')}</option>
          {tracks.map((track) => (
            <option key={track.id} value={track.id}>
              {`${trackCode(track, severalMedia)} ${track.name}` + (durationText(track.length_ms) ? ` (${durationText(track.length_ms)})` : '')}
            </option>
          ))}
        </select>
        {choice.kind === 'none' && proposal !== undefined && (
          <p className="text-xs text-mist-500">{t('downloads.albumAssign.proposal', { track: `${trackCode(proposal, severalMedia)} ${proposal.name}` })}</p>
        )}
        {choice.kind === 'loose' && <p className="text-xs text-mist-500">{t('downloads.albumAssign.looseHint')}</p>}
      </div>
    </li>
  )
}

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { musicApi } from '../../api/music'
import type { AlbumTagPreview } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, Spinner } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { tagFieldText, tagRefusalText } from './tagText'

/**
 * "Tags prüfen" (M4, Entscheidung 29): je Datei die Felder, die sich aendern wuerden, die MusicBrainz-Kennungen
 * eingeschlossen, und warum eine Datei nicht geschrieben werden kann. "Tags schreiben" schreibt alle mit Aenderungen;
 * danach zeigt der Dialog den Stand neu.
 */
export function TagPreviewDialog({ titleId, onClose, onWritten }: { titleId: number; onClose: () => void; onWritten: () => void }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [preview, setPreview] = useState<AlbumTagPreview | null>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const abort = new AbortController()
    musicApi.tagPreview(titleId, abort.signal).then(
      (result) => {
        if (!abort.signal.aborted) setPreview(result)
      },
      (error: unknown) => {
        if (!abort.signal.aborted) setProblem(error)
      },
    )
    return () => abort.abort()
  }, [titleId])

  async function write() {
    setBusy(true)
    setProblem(null)
    try {
      setPreview(await musicApi.writeTags(titleId))
      onWritten()
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  const changed = preview?.files.filter((file) => file.changes.length > 0) ?? []
  const refused = preview?.files.filter((file) => file.refusal !== null) ?? []

  return (
    <Dialog
      open
      wide
      title={t('title.album.tags.title')}
      onClose={() => {
        if (!busy) onClose()
      }}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t('common.actions.close')}
          </Button>
          <Button onClick={() => void write()} loading={busy} disabled={preview === null || (preview.changed === 0 && !preview.cover)}>
            {!busy && <Symbol name="check" />}
            {t('title.album.tags.write')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4 text-sm">
        {preview === null && problem === null && (
          <p className="flex items-center gap-2 text-mist-500" role="status">
            <Spinner />
            {t('title.album.tags.loading')}
          </p>
        )}
        {preview !== null && (
          <>
            {typeof preview.written === 'number' && (
              <p role="status" className="flex items-center gap-2 text-mist-200">
                <Symbol name="check" className="h-4 w-4 text-ok-500" />
                {t('title.album.tags.written', { count: preview.written, value: formatNumber(preview.written, language) })}
              </p>
            )}
            <p className="text-mist-300">
              {preview.changed > 0
                ? t('title.album.tags.changed', { count: preview.changed, value: formatNumber(preview.changed, language) })
                : t('title.album.tags.nothing')}{' '}
              {preview.cover ? t('title.album.tags.cover') : t('title.album.tags.noCover')}
            </p>
            {!preview.write_enabled && <p className="text-mist-400">{t('title.album.tags.switchOff')}</p>}
            {changed.length > 0 && (
              <ul className="flex flex-col gap-3" aria-label={t('title.album.tags.filesTitle')}>
                {changed.map((file) => (
                  <li key={file.track_file_id} className="flex min-w-0 flex-col gap-1.5 rounded-xl border border-ink-700 bg-ink-900/60 p-3">
                    <p className="font-mono text-xs break-all text-mist-100">{file.file}</p>
                    {file.refusal !== null && <p className="text-xs text-bad-500">{tagRefusalText(t, file.refusal)}</p>}
                    <dl className="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
                      {file.changes.map((change) => (
                        <div key={change.field} className="contents">
                          <dt className="text-mist-400">{tagFieldText(t, change.field)}</dt>
                          <dd className="min-w-0 wrap-anywhere text-mist-200">
                            <span className="text-mist-500 line-through">{change.before.join('; ') || t('title.album.tags.empty')}</span>{' '}
                            <span>{change.after.join('; ') || t('title.album.tags.empty')}</span>
                          </dd>
                        </div>
                      ))}
                    </dl>
                  </li>
                ))}
              </ul>
            )}
            {refused.length > 0 && changed.length === 0 && (
              <ul className="flex flex-col gap-1">
                {refused.map((file) => (
                  <li key={file.track_file_id} className="text-xs text-mist-400">
                    <span className="font-mono">{file.file}</span>: {tagRefusalText(t, file.refusal ?? '')}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

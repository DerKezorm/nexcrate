import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { downloadsApi } from '../../api/downloads'
import type { Download, DownloadVideo } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, FormMessage, Spinner } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { sizeText } from '../../lib/size'

/**
 * "Datei waehlen" fuer einen Film mit mehreren aehnlich grossen Videos (S4, Entscheidung 39): die Videos mit Groesse und
 * Laufzeit, vorbelegt das groesste. Danach legt nexcrate wie immer ab.
 */
export function ChooseDialog({ download, onClose, onDone }: { download: Download; onClose: () => void; onDone: (message: string) => void }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [videos, setVideos] = useState<DownloadVideo[] | null>(null)
  const [chosen, setChosen] = useState<number | null>(null)
  const [loadProblem, setLoadProblem] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  useEffect(() => {
    const abort = new AbortController()
    downloadsApi.files(download.id, abort.signal).then(
      (result) => {
        if (abort.signal.aborted) return
        const candidates = result.files.filter((file) => file.decision === 'candidate' || file.decision === 'chosen')
        setVideos(candidates)
        const largest = [...candidates].sort((a, b) => b.size_bytes - a.size_bytes)[0]
        setChosen(largest ? largest.key : null)
      },
      (error: unknown) => {
        if (!abort.signal.aborted) setLoadProblem(error)
      },
    )
    return () => abort.abort()
  }, [download.id])

  async function submit() {
    if (chosen === null) return
    setBusy(true)
    setProblem(null)
    try {
      await downloadsApi.choose(download.id, chosen)
      onDone(t('downloads.choose.done'))
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  function close() {
    if (!busy) onClose()
  }

  return (
    <Dialog
      open
      wide
      title={t('downloads.choose.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void submit()} loading={busy} disabled={chosen === null}>
            {t('downloads.choose.submit')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm wrap-anywhere text-mist-400">{t('downloads.choose.intro', { release: download.release.title })}</p>
        {loadProblem !== null && <FormMessage>{errorText(t, loadProblem)}</FormMessage>}
        {videos === null && loadProblem === null && (
          <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
            <Spinner />
            {t('downloads.assign.loading')}
          </p>
        )}
        {videos !== null && (
          <fieldset className="flex flex-col gap-2">
            <legend className="sr-only">{t('downloads.choose.title')}</legend>
            {videos.map((video) => (
              <label
                key={video.key}
                className={
                  'flex min-w-0 cursor-pointer items-start gap-3 rounded-xl border p-3 ' +
                  (chosen === video.key ? 'border-accent-500/60 bg-accent-500/10' : 'border-ink-700 bg-ink-900/60 hover:border-accent-500/50')
                }
              >
                <input type="radio" name="video" className="mt-1 accent-accent-500" checked={chosen === video.key} onChange={() => setChosen(video.key)} />
                <span className="flex min-w-0 flex-col gap-1">
                  <span className="font-mono text-xs break-all text-mist-100">{video.path}</span>
                  <span className="flex flex-wrap gap-x-3 text-xs text-mist-500 tabular-nums">
                    <span>{sizeText(t, video.size_bytes, language)}</span>
                    {video.duration_seconds !== null && <span>{t('downloads.assign.duration', { minutes: formatNumber(Math.round(video.duration_seconds / 60), language) })}</span>}
                  </span>
                </span>
              </label>
            ))}
          </fieldset>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

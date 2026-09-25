import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { downloadsApi } from '../../api/downloads'
import type { Download } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, FormMessage } from '../../components/ui'
import { clientNameOf } from './downloadText'

/**
 * "Rest nicht ablegen" mit Rueckfrage (seit der Durchsicht): welche Folgen ohne Datei bleiben, dass sie bei der naechsten
 * Suche wiederkommen, und was mit den Dateien im Download-Programm geschieht. SABnzbd loescht den Job samt Resten, ein
 * Torrent bleibt und teilt weiter (S4, Entscheidung 30).
 */
export function FinishDialog({ download, onClose, onDone }: { download: Download; onClose: () => void; onDone: (message: string) => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const waiting = download.scope?.waiting_codes ?? []
  const client = clientNameOf(t, download)

  async function finish() {
    setBusy(true)
    setProblem(null)
    try {
      await downloadsApi.finish(download.id)
      onDone(t('downloads.assign.finished'))
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
      title={t('downloads.finish.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void finish()} loading={busy}>
            {t('downloads.finish.confirm')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3 text-sm text-mist-300">
        {download.scope?.kind === 'album' && <p>{t('downloads.finish.album')}</p>}
        {waiting.length > 0 && (
          <p>
            {t('downloads.finish.waiting', { codes: waiting.join(', ') })} {t('downloads.finish.again')}
          </p>
        )}
        {download.client !== null && <p>{download.protocol === 'torrent' ? t('downloads.finish.torrent', { client }) : t('downloads.finish.usenet', { client })}</p>}
        <p className="font-mono text-xs leading-5 wrap-anywhere text-mist-400">{download.release.title}</p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

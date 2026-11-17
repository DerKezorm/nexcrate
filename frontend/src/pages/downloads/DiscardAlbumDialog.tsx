import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { downloadsApi } from '../../api/downloads'
import type { Download } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, FormMessage } from '../../components/ui'

/**
 * Seit 17.11.2026: einen Album-Download komplett verwerfen, mit Rueckfrage. Was er abgelegt hat, verlaesst das Album
 * wieder, das Release wird gesperrt, der Auftrag geht samt Dateien, und nexcrate sucht ein anderes Release. Anders als
 * "Entfernen", das Abgelegtes im Album laesst und nichts sucht.
 */
export function DiscardAlbumDialog({ download, onClose, onDiscarded }: { download: Download; onClose: () => void; onDiscarded: () => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const filed = download.scope?.filed ?? 0

  async function discard() {
    setBusy(true)
    setProblem(null)
    try {
      await downloadsApi.discardAlbum(download.id)
      onDiscarded()
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
      title={t('downloads.discard.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="danger" onClick={() => void discard()} loading={busy}>
            {t('downloads.discard.confirm')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-mist-300">{t('downloads.discard.text', { count: filed, value: String(filed) })}</p>
        <p className="text-sm text-mist-400">{t('downloads.discard.notRemove')}</p>
        <p className="font-mono text-xs leading-5 wrap-anywhere text-mist-400">{download.release.title}</p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

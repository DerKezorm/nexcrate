import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { downloadsApi } from '../../api/downloads'
import type { Download } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, FormMessage, Toggle } from '../../components/ui'

/**
 * Einen Download entfernen, mit Rueckfrage. Ob er auch im Programm verschwindet und ob das Release
 * gesperrt wird, entscheidet man hier. Aus dem Programm entfernen ist vorgewaehlt, ausser der
 * Download ist dort schon verschwunden; sperren nur, wenn man "Entfernen und sperren" gewaehlt hat.
 * Abgelegte Dateien fasst der Server dabei nie an.
 */
export function RemoveDownloadDialog({
  download,
  blocklist: blockAtStart,
  onClose,
  onRemoved,
}: {
  download: Download
  blocklist: boolean
  onClose: () => void
  onRemoved: () => void
}) {
  const { t } = useTranslation()
  const client = download.client
  const [fromClient, setFromClient] = useState(client !== null && download.problem?.code !== 'gone_from_client')
  const [blocklist, setBlocklist] = useState(blockAtStart)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function remove() {
    setBusy(true)
    setProblem(null)
    try {
      await downloadsApi.remove(download.id, { remove_from_client: client !== null && fromClient, blocklist })
      onRemoved()
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
      title={t('downloads.remove.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="danger" onClick={() => void remove()} loading={busy}>
            {t('downloads.remove.confirm')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-mist-300">{t('downloads.remove.text')}</p>
        <p className="font-mono text-xs leading-5 wrap-anywhere text-mist-400">{download.release.title}</p>
        {client !== null && (
          <Toggle
            label={t('downloads.remove.fromClient', { client: client.name })}
            hint={t('downloads.remove.fromClientHint', { client: client.name })}
            checked={fromClient}
            onChange={setFromClient}
          />
        )}
        <Toggle label={t('downloads.remove.blocklist')} hint={t('downloads.remove.blocklistHint')} checked={blocklist} onChange={setBlocklist} />
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

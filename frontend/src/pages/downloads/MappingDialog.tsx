import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { downloadsApi } from '../../api/downloads'
import type { Download, PathMapping } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage } from '../../components/ui'
import { clientNameOf } from './downloadText'

/** Derselbe Ordner zweimal: wie das Programm ihn meldet und wie nexcrate ihn sieht. Nebeneinander, schmal untereinander. */
export function PathPair({ mapping }: { mapping: PathMapping }) {
  const { t } = useTranslation()
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] items-center gap-2 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
      <div className="min-w-0 rounded-lg border border-ink-700 bg-ink-850 px-3 py-2">
        <p className="text-xs text-mist-500">{t('downloads.problems.clientSees')}</p>
        <p className="font-mono text-sm break-all text-mist-100">{mapping.remote}</p>
      </div>
      <Symbol name="arrow" className="h-4 w-4 rotate-90 justify-self-center text-mist-500 sm:rotate-0" />
      <div className="min-w-0 rounded-lg border border-ink-700 bg-ink-850 px-3 py-2">
        <p className="text-xs text-mist-500">{t('downloads.problems.nexcrateSees')}</p>
        <p className="font-mono text-sm break-all text-mist-100">{mapping.local}</p>
      </div>
    </div>
  )
}

/**
 * Die vorgeschlagene Zuordnung vor dem Uebernehmen. Sie gilt fuer alle Downloads des Programms und
 * bleibt gespeichert, deshalb mit Rueckfrage. Danach versucht der Server das Ablegen gleich noch einmal.
 */
export function MappingDialog({ download, mapping, onClose, onDone }: { download: Download; mapping: PathMapping; onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const client = clientNameOf(t, download)

  async function take() {
    setBusy(true)
    setProblem(null)
    try {
      await downloadsApi.confirmMapping(download.id)
      onDone()
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
      title={t('downloads.mapping.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void take()} loading={busy}>
            {t('downloads.mapping.submit')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4 text-sm">
        <p className="wrap-anywhere text-mist-300">{t('downloads.mapping.intro', { client, remote: mapping.remote, local: mapping.local })}</p>
        <PathPair mapping={mapping} />
        <p className="text-mist-400">{t('downloads.mapping.scope', { client })}</p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

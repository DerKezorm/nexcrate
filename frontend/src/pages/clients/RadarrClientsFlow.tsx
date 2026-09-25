import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { Source } from '../../api/types'
import { useNotice } from '../../components/useNotice'
import { ClientDialog, type ClientTemplate } from './ClientDialog'
import { RadarrClientsDialog } from './RadarrClientsDialog'

/**
 * "Download-Programme holen" neben einer Verbindung im Reiter Import: erst die Liste aus Radarr, dann fuer den
 * gewaehlten Eintrag der Dialog zum Eintragen, wie unter Download-Programme. ⚠️ `sources` muss gleich bleiben, solange
 * das Fenster offen ist: Die Liste liest bei jeder neuen Liste neu.
 */
export function RadarrClientsFlow({ sources, onClose }: { sources: Source[]; onClose: () => void }) {
  const { t } = useTranslation()
  const notify = useNotice()
  const [template, setTemplate] = useState<ClientTemplate | null>(null)

  if (template === null) return <RadarrClientsDialog sources={sources} onClose={onClose} onPick={setTemplate} />
  return (
    <ClientDialog
      client={null}
      template={template}
      onClose={onClose}
      onSaved={(saved) => {
        notify(t('settings.clients.radarr.added', { name: saved.name }))
        onClose()
      }}
    />
  )
}

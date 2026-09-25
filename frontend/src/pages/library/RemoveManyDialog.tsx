import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { libraryApi } from '../../api/library'
import type { MediaKind, RemovedMany } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, FormMessage, Toggle } from '../../components/ui'
import { formatNumber } from '../../lib/format'

/** Der Umfang: die markierten Titel oder die ganze Ansicht. */
export type RemoveScope = {
  kind: MediaKind
  ids: number[] | null
  state: string | null
  q: string | null
  tag: string | null
}

/**
 * "Entfernen" fuer die Auswahl (entschieden am 24.09.2026). Ohne Haken verschwinden nur die Eintraege, die Dateien
 * bleiben liegen; mit Haken kommen die Dateien vorher in den Papierkorb, von wo sie fuer die eingestellte Zeit
 * zurueckgeholt werden koennen. Laufende Downloads der Titel gehen mit.
 */
export function RemoveManyDialog({
  scope,
  count,
  onClose,
  onDone,
}: {
  scope: RemoveScope
  count: number
  onClose: () => void
  onDone: (result: RemovedMany) => void
}) {
  const { t, i18n } = useTranslation()
  const [files, setFiles] = useState(false)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const value = formatNumber(count, i18n.language)

  async function apply() {
    setBusy(true)
    setProblem(null)
    try {
      onDone(
        await libraryApi.removeMany({
          kind: scope.kind,
          title_ids: scope.ids,
          state: scope.state,
          q: scope.q,
          tag: scope.tag,
          delete_files: files,
        }),
      )
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  return (
    <Dialog
      open
      title={t('library.select.remove.title', { count, value })}
      onClose={() => !busy && onClose()}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="danger" onClick={() => void apply()} loading={busy}>
            {files ? t('library.select.remove.applyWithFiles', { count, value }) : t('library.select.remove.apply', { count, value })}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm text-mist-300">{t('library.select.remove.intro')}</p>
        <Toggle label={t('library.select.remove.files')} hint={t('library.select.remove.filesHint')} checked={files} onChange={setFiles} disabled={busy} />
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

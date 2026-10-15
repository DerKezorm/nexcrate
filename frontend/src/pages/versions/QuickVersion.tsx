import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import type { MediaKind, Version } from '../../api/types'
import { versionsApi } from '../../api/versions'
import { Symbol } from '../../components/Symbol'
import { Button, Field, FormMessage } from '../../components/ui'

/**
 * Eine Fassung gleich dort anlegen, wo sie fehlt: auf der Seite "Ordner einlesen" und in den Dialogen zum Zuordnen.
 * Wer ohne Fassung einliest, stand sonst vor einer leeren Bibliothek und einem Verweis in die Einstellungen (Befund
 * vom 15.10.2026). Mit `folder` wird der Ordner auf Wunsch gleich ihr Standardordner; scheitert nur das, gibt es die
 * Fassung trotzdem, und `onCreated` bekommt den Fehler mit.
 */
export function QuickVersion({ kind, folder = null, onCreated }: { kind: MediaKind; folder?: string | null; onCreated: (version: Version, folderProblem: unknown) => void }) {
  const { t } = useTranslation()
  const checkId = useId()
  const [label, setLabel] = useState(() => (kind === 'album' ? t('disk.quickVersion.defaultMusic') : t('disk.quickVersion.defaultVideo')))
  const [useFolder, setUseFolder] = useState(true)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function create() {
    if (busy) return
    const clean = label.trim()
    if (clean === '') {
      setProblem(t('settings.versions.missingLabel'))
      return
    }
    setBusy(true)
    setProblem(null)
    let created: Version
    try {
      created = await versionsApi.create({ kind, label: clean })
    } catch (error) {
      setProblem(errorText(t, error))
      setBusy(false)
      return
    }
    let folderProblem: unknown = null
    if (folder !== null && useFolder) {
      try {
        created = await versionsApi.setFolder(created.id, folder)
      } catch (error) {
        folderProblem = error
      }
    }
    setBusy(false)
    onCreated(created, folderProblem)
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="min-w-0 flex-1">
          <Field
            label={t('settings.versions.label')}
            hint={t('settings.versions.labelHint')}
            value={label}
            maxLength={64}
            onChange={(event) => setLabel(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                void create()
              }
            }}
          />
        </div>
      </div>
      {folder !== null && (
        <label htmlFor={checkId} className="flex items-start gap-2 text-sm text-mist-300">
          <input id={checkId} type="checkbox" checked={useFolder} onChange={(event) => setUseFolder(event.target.checked)} className="mt-0.5 accent-accent-500" />
          <span className="min-w-0 wrap-anywhere">{t('disk.quickVersion.useFolder', { folder })}</span>
        </label>
      )}
      {problem !== null && <FormMessage>{problem}</FormMessage>}
      <Button size="sm" className="w-fit" onClick={() => void create()} loading={busy}>
        <Symbol name="plus" />
        {t('disk.quickVersion.create')}
      </Button>
    </div>
  )
}

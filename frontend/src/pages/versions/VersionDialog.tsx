import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import type { MediaKind, Version } from '../../api/types'
import { versionsApi } from '../../api/versions'
import { Dialog } from '../../components/Dialog'
import { Button, Field, FormMessage } from '../../components/ui'

/**
 * Eine Fassung anlegen (`version` null) oder umbenennen. Mehr als ein Name gehoert
 * in Schritt 1 nicht dazu: Profil und Ordner kommen spaeter aus nexcrate selbst, nie
 * als Freitext. Gerendert wird der Dialog nur, solange er offen ist.
 */
export function VersionDialog({
  kind,
  version,
  onClose,
  onSaved,
}: {
  kind: MediaKind
  version: Version | null
  onClose: () => void
  onSaved: (saved: Version) => void
}) {
  const { t } = useTranslation()
  const [label, setLabel] = useState(version?.label ?? '')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  function submit(event: FormEvent) {
    event.preventDefault()
    void save()
  }

  async function save() {
    if (busy) return
    const cleanLabel = label.trim()
    if (cleanLabel === '') {
      setProblem(t('settings.versions.missingLabel'))
      return
    }
    setBusy(true)
    setProblem(null)
    try {
      const saved = version ? await versionsApi.update(version.id, { label: cleanLabel }) : await versionsApi.create({ kind, label: cleanLabel })
      onSaved(saved)
    } catch (error) {
      // version_label_taken und invalid_input kommen mit Text aus errors.json.
      setProblem(errorText(t, error))
      setBusy(false)
    }
  }

  function close() {
    if (!busy) onClose()
  }

  return (
    <Dialog
      open
      title={version ? t('settings.versions.dialogEdit', { label: version.label }) : t('settings.versions.dialogAdd')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void save()} loading={busy}>
            {t('common.actions.save')}
          </Button>
        </>
      }
    >
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field
          label={t('settings.versions.label')}
          hint={t('settings.versions.labelHint')}
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          maxLength={200}
          autoComplete="off"
          autoFocus
        />
        {problem && <FormMessage>{problem}</FormMessage>}
      </form>
    </Dialog>
  )
}

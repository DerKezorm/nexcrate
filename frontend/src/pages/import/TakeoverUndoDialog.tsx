import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import { takeoverApi } from '../../api/takeover'
import type { ImportRun, Source } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { PasswordField } from '../../components/PasswordField'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage } from '../../components/ui'

/**
 * "Rückgängig machen" fuer eine uebernommene Verbindung: was danach passiert, wenn noetig der API-Schluessel, dann
 * `POST /api/sources/{id}/takeover/undo`. Der Server antwortet mit dem Import, den er gleich startet; die Seite reicht
 * ihn an die Karte weiter, die ihm folgt wie nach "Jetzt importieren".
 *
 * ⚠️ Ein Feld fuer den Schluessel gibt es nur, wenn keiner gespeichert ist (Uebernahmen von vor Befund 12 haben ihn
 * geloescht) oder der Server den gespeicherten nicht lesen kann (`source_key_missing`). Sonst geht ein leerer Koerper
 * hinaus, und der gespeicherte gilt. Das Feld ist nie vorbelegt und wird nach dem Absenden und beim Schliessen geleert.
 */
export function TakeoverUndoDialog({
  source,
  label,
  onClose,
  onUndone,
}: {
  source: Source
  /** Die Fassung, die diese Verbindung fuellt. null, wenn die Liste der Fassungen sie nicht kennt; dann fehlt der Satz zur Benennung. */
  label: string | null
  onClose: () => void
  onUndone: (run: ImportRun) => void
}) {
  const { t } = useTranslation()
  const [askKey, setAskKey] = useState(!source.has_api_key)
  const [apiKey, setApiKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const key = apiKey.trim()
  const missingKey = askKey && key === ''

  async function undo() {
    if (busy || missingKey) return
    setBusy(true)
    setProblem(null)
    try {
      const run = await takeoverApi.undo(source.id, askKey ? { api_key: key } : {})
      setApiKey('')
      onUndone(run)
    } catch (error) {
      // Laesst sich der gespeicherte Schluessel nicht lesen, fragt das Fenster ihn ab. Sonst ginge es nicht weiter.
      if (error instanceof ApiError && error.code === 'source_key_missing') setAskKey(true)
      setProblem(error)
      setBusy(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void undo()
  }

  function close() {
    if (busy) return
    setApiKey('')
    onClose()
  }

  const series = source.app === 'sonarr'
  const music = source.app === 'lidarr'
  const lines = [
    music
      ? t('import.takeover.music.undo.reads', { name: source.name })
      : series
        ? t('import.takeover.series.undo.reads', { name: source.name })
        : t('import.takeover.undo.reads', { name: source.name }),
    music ? t('import.takeover.music.undo.loaded') : series ? t('import.takeover.series.undo.loaded') : t('import.takeover.undo.loaded'),
    // Library from disk: the release.nex files of the versions handed back go again, changed ones stay.
    music ? t('import.takeover.music.undo.companion') : series ? t('import.takeover.series.undo.companion') : t('import.takeover.undo.companion'),
    ...(label !== null ? [t('import.takeover.undo.naming', { label })] : []),
  ]

  return (
    <Dialog
      open
      title={t('import.takeover.undo.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void undo()} loading={busy} disabled={missingKey}>
            {t('import.takeover.undo.submit')}
          </Button>
        </>
      }
    >
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <ul className="flex flex-col gap-2 text-sm text-mist-200">
          {lines.map((line) => (
            <li key={line} className="flex items-start gap-2">
              <Symbol name="info" className="mt-0.5 h-4 w-4 shrink-0 text-info-500" />
              <span className="min-w-0 wrap-anywhere">{line}</span>
            </li>
          ))}
        </ul>
        {askKey && (
          <PasswordField
            label={t('import.sources.apiKey')}
            hint={source.has_api_key ? (series ? t('series.import.apiKeyHint') : t('import.sources.apiKeyHint')) : t('import.takeover.undo.keyDeleted')}
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            autoComplete="new-password"
            maxLength={200}
            autoFocus
          />
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </form>
    </Dialog>
  )
}

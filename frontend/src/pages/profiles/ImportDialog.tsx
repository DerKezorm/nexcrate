import { useId, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import { PROFILE_IMPORT_MAX_BYTES, profilesApi } from '../../api/profiles'
import type { MediaKind, ProfileImport } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, FormMessage } from '../../components/ui'
import { ProfileSummaryView } from './ProfileSummary'
import { invalidAnswersText, listValues, questionNames, shortCommit } from './profileText'

type Step = 'input' | 'preview' | 'replace'

/**
 * Ein Profil aus einer YAML-Datei: Datei waehlen oder Inhalt einfuegen, pruefen lassen, die
 * Zusammenfassung ansehen, dann uebernehmen. Gespeichert wird ueber dasselbe PUT wie im
 * Assistenten. Hat die Fassung schon ein Profil, fragt der Dialog vor dem Ersetzen.
 *
 * ⚠️ Groesser als 64 KB geht nichts hinaus, weder als Datei noch eingefuegt. Der Server wuerde
 * es ohnehin abweisen.
 */
export function ImportDialog({
  profileId,
  kind,
  label,
  filled,
  onClose,
  onDone,
}: {
  profileId: number
  kind: MediaKind
  /** Wie das Profil heisst; steht in der Ueberschrift. */
  label: string
  /** Steht schon etwas darin? Dann fragt der Dialog vor dem Ersetzen. */
  filled: boolean
  onClose: () => void
  onDone: () => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const fileId = useId()
  const textId = useId()
  const [text, setText] = useState('')
  const [fileName, setFileName] = useState<string | null>(null)
  const [result, setResult] = useState<ProfileImport | null>(null)
  const [step, setStep] = useState<Step>('input')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function pickFile(file: File | undefined) {
    setProblem(null)
    if (!file) return
    if (!/\.ya?ml$/i.test(file.name)) return setProblem(t('profiles.import.wrongType'))
    // Die Datei wird dann gar nicht erst gelesen.
    if (file.size > PROFILE_IMPORT_MAX_BYTES) return setProblem(t('profiles.import.tooLarge'))
    try {
      setText(await file.text())
      setFileName(file.name)
    } catch {
      setProblem(t('profiles.import.unreadable'))
    }
  }

  /**
   * `profile_import_invalid {reason}` mit einem Satz je Grund. Die Gruende stehen im Server in
   * `services/profiles/files.py`; einen neuen, den die Oberflaeche nicht kennt, sagt sie allgemein.
   */
  function importProblem(error: unknown): string {
    if (error instanceof ApiError && error.code === 'profile_import_invalid') {
      const reason = typeof error.values.reason === 'string' ? error.values.reason : ''
      if (reason === 'answers_invalid') {
        return listValues(error.values.fields).length > 0
          ? t('profiles.import.reasons.answers_invalid', { fields: questionNames(t, i18n, error.values.fields, language) })
          : t('profiles.import.reasons.answers_invalidPlain')
      }
      const key = `profiles.import.reasons.${reason}`
      return reason !== '' && i18n.exists(key) ? t(key) : t('profiles.import.invalid')
    }
    if (error instanceof ApiError && error.code === 'profile_answers_invalid') return invalidAnswersText(t, i18n, error, language)
    return errorText(t, error)
  }

  async function check() {
    if (busy) return
    if (text.trim() === '') return setProblem(t('profiles.import.empty'))
    if (new TextEncoder().encode(text).length > PROFILE_IMPORT_MAX_BYTES) return setProblem(t('profiles.import.tooLarge'))
    setBusy(true)
    setProblem(null)
    try {
      setResult(await profilesApi.importFile({ yaml: text, kind }))
      setStep('preview')
    } catch (error) {
      setProblem(importProblem(error))
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    if (busy || result === null) return
    setBusy(true)
    setProblem(null)
    try {
      await profilesApi.saveProfile(profileId, result.answers)
      onDone()
    } catch (error) {
      setProblem(importProblem(error))
      setBusy(false)
    }
  }

  function takeOver() {
    setProblem(null)
    if (filled) setStep('replace')
    else void save()
  }

  function close() {
    if (!busy) onClose()
  }

  let body: ReactNode
  let footer: ReactNode
  if (step === 'replace') {
    body = (
      <div className="flex flex-col gap-3">
        <p className="text-sm text-mist-300">{t('profiles.import.replaceText', { label: label })}</p>
        {problem && <FormMessage>{problem}</FormMessage>}
      </div>
    )
    footer = (
      <>
        <Button
          variant="ghost"
          onClick={() => {
            setStep('preview')
            setProblem(null)
          }}
          disabled={busy}
        >
          {t('common.actions.cancel')}
        </Button>
        <Button variant="danger" onClick={() => void save()} loading={busy}>
          {t('profiles.import.replaceConfirm')}
        </Button>
      </>
    )
  } else if (step === 'preview' && result !== null) {
    body = (
      <div className="flex flex-col gap-4">
        <p className="text-sm text-mist-300">{t('profiles.import.previewIntro', { label: label })}</p>
        {result.name && <p className="text-xs wrap-anywhere text-mist-500">{t('profiles.import.from', { name: result.name })}</p>}
        {result.commit_differs && <FormMessage tone="info">{t('profiles.import.commitDiffers', { commit: shortCommit(result.trash_commit) })}</FormMessage>}
        <ProfileSummaryView summary={result.summary} />
        {problem && <FormMessage>{problem}</FormMessage>}
      </div>
    )
    footer = (
      <>
        <Button
          variant="ghost"
          onClick={() => {
            setStep('input')
            setProblem(null)
          }}
          disabled={busy}
        >
          {t('profiles.import.otherFile')}
        </Button>
        <Button onClick={takeOver} loading={busy}>
          {t('profiles.import.save')}
        </Button>
      </>
    )
  } else {
    body = (
      <div className="flex flex-col gap-4">
        <p className="text-sm text-mist-400">{t('profiles.import.intro')}</p>
        <div className="flex flex-col gap-1.5">
          <label htmlFor={fileId} className="text-sm font-medium text-mist-300">
            {t('profiles.import.file')}
          </label>
          <input
            id={fileId}
            type="file"
            accept=".yaml,.yml"
            aria-describedby={`${fileId}-hint`}
            onChange={(event) => void pickFile(event.target.files?.[0])}
            className="block w-full min-w-0 text-sm text-mist-400 file:mr-3 file:rounded-full file:border file:border-ink-700 file:bg-ink-850 file:px-3.5 file:py-1.5 file:text-xs file:font-semibold file:text-mist-300 hover:file:bg-ink-800"
          />
          <p id={`${fileId}-hint`} className="text-xs wrap-anywhere text-mist-500">
            {fileName ? t('profiles.import.fileChosen', { name: fileName }) : t('profiles.import.fileHint')}
          </p>
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor={textId} className="text-sm font-medium text-mist-300">
            {t('profiles.import.paste')}
          </label>
          <textarea
            id={textId}
            value={text}
            rows={8}
            spellCheck={false}
            onChange={(event) => {
              setText(event.target.value)
              setFileName(null)
              setProblem(null)
            }}
            className="w-full min-w-0 rounded-xl border border-ink-700 bg-ink-900 px-4 py-3 font-mono text-xs text-mist-100 focus:border-accent-500 focus:outline-none"
          />
        </div>
        {problem && <FormMessage>{problem}</FormMessage>}
      </div>
    )
    footer = (
      <>
        <Button variant="ghost" onClick={close} disabled={busy}>
          {t('common.actions.cancel')}
        </Button>
        <Button onClick={() => void check()} loading={busy}>
          {t('profiles.import.check')}
        </Button>
      </>
    )
  }

  return (
    <Dialog
      open
      wide
      title={step === 'replace' ? t('profiles.import.replaceTitle', { label: label }) : t('profiles.import.title', { label: label })}
      onClose={close}
      footer={footer}
    >
      {body}
    </Dialog>
  )
}

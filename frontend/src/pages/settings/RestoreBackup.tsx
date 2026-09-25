import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { backupsApi, setupBackupApi, type ArchiveInfo } from '../../api/backups'
import { errorText } from '../../api/client'
import { systemApi } from '../../api/system'
import { Button, Field, FormMessage } from '../../components/ui'
import { formatDateTime } from '../../lib/format'
import { RestartWaiting } from './RestartWaiting'

/**
 * Welche Warnung zum Schluessel gilt, oder keine. Zwei Fragen, nicht eine (Befund aus Nexview): liegt im Archiv ein
 * Schluessel, und nimmt diese Installation ihren aus der Umgebung. Archiv ohne Schluessel und Ziel ohne Variable ist
 * der schlimmste Fall: danach ist kein gespeicherter Zugang mehr lesbar.
 */
function keyWarning(info: ArchiveInfo): { text: 'restore.keyOnlyFromEnv' | 'restore.noKeyAtAll' | 'restore.envKeyWarning'; heavy: boolean } | null {
  if (!info.key_in_archive) return info.key_from_environment ? { text: 'restore.keyOnlyFromEnv', heavy: false } : { text: 'restore.noKeyAtAll', heavy: true }
  return info.key_from_environment ? { text: 'restore.envKeyWarning', heavy: false } : null
}

/**
 * Eine Sicherung einspielen, in zwei Schritten: erst ansehen (liest nur), dann einspielen. Danach startet nexcrate
 * neu und spielt das Archiv ein, bevor irgendetwas die Datenbank oeffnet; die Seite wartet und laedt dann neu.
 *
 * `fresh`: im Einrichtungsassistenten einer frischen Installation. Dort gibt es noch nichts zu verlieren, der Ton ist
 * ruhiger, und die Adressen sind die der Einrichtung (offen nur, solange es kein Konto gibt).
 */
export function RestoreBackup({ fresh = false }: { fresh?: boolean }) {
  const { t, i18n } = useTranslation()
  const fileId = useId()
  const calls = fresh ? setupBackupApi : backupsApi
  const [file, setFile] = useState<File | null>(null)
  const [password, setPassword] = useState('')
  const [info, setInfo] = useState<ArchiveInfo | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  /** Die Startzeit des Servers vor dem Einspielen; solange sie gleich bleibt, laeuft noch der alte Prozess. */
  const [restarting, setRestarting] = useState<string | null>(null)

  async function check() {
    if (file === null) return
    setBusy(true)
    setProblem(null)
    try {
      setInfo(await calls.check(file, password))
    } catch (error) {
      setInfo(null)
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  async function restore() {
    if (file === null) return
    setBusy(true)
    setProblem(null)
    try {
      const before = await systemApi.health()
      await calls.restore(file, password)
      setRestarting(before.started)
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  if (restarting !== null) return <RestartWaiting started={restarting} />

  const warning = info === null ? null : keyWarning(info)

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <label className="text-sm font-medium text-mist-300" htmlFor={fileId}>
          {t('restore.file')}
        </label>
        <input
          id={fileId}
          type="file"
          accept=".zip,application/zip"
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null)
            setInfo(null)
            setProblem(null)
          }}
          className="rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 file:mr-3 file:rounded-full file:border-0 file:bg-ink-800 file:px-3 file:py-1 file:text-sm file:text-mist-300"
        />
      </div>
      <Field
        label={t('restore.password')}
        hint={t('restore.passwordHint')}
        type="password"
        autoComplete="off"
        value={password}
        onChange={(event) => {
          setPassword(event.target.value)
          setInfo(null)
        }}
      />
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}

      {info === null ? (
        <Button className="self-start" disabled={file === null || password.length === 0} loading={busy} onClick={() => void check()}>
          {t('restore.check')}
        </Button>
      ) : (
        <div className="flex flex-col gap-4 rounded-xl border border-ink-700 bg-ink-900 p-4">
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
            <dt className="text-mist-500">{t('restore.fromWhen')}</dt>
            <dd className="text-mist-100">{info.created ? formatDateTime(info.created, i18n.language) : '·'}</dd>
            <dt className="text-mist-500">{t('restore.fromVersion')}</dt>
            <dd className="text-mist-100">{info.version || '·'}</dd>
            <dt className="text-mist-500">{t('restore.note')}</dt>
            <dd className="wrap-anywhere text-mist-100">{info.comment || '·'}</dd>
          </dl>
          {!info.restorable && (
            <FormMessage>{info.reason === 'backup_newer' ? t('restore.tooNew', { version: info.version }) : t('restore.unknownVersion')}</FormMessage>
          )}
          {/* nexcrate holt nur sich selbst zurueck: was inzwischen in den Download-Programmen oder auf der Platte geschah, bleibt. */}
          {!fresh && <FormMessage tone="info">{t('restore.outside')}</FormMessage>}
          {warning !== null && <FormMessage tone={warning.heavy ? 'bad' : 'info'}>{t(warning.text)}</FormMessage>}
          {info.restorable && (fresh ? <FormMessage tone="info">{t('restore.freshNote')}</FormMessage> : <FormMessage>{t('restore.replaceWarning')}</FormMessage>)}
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" onClick={() => setInfo(null)} disabled={busy}>
              {t('common.actions.cancel')}
            </Button>
            <Button variant="danger" disabled={!info.restorable} loading={busy} onClick={() => void restore()}>
              {t('restore.restoreNow')}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

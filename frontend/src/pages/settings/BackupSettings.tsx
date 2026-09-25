import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import type { TFunction } from 'i18next'

import { BACKUP_SCHEDULES, backupsApi, type Backup, type BackupKind, type BackupList, type BackupSchedule } from '../../api/backups'
import { errorText } from '../../api/client'
import { systemApi } from '../../api/system'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, Field, FormMessage, Section, SelectField, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatDateTime } from '../../lib/format'
import { RestartWaiting } from './RestartWaiting'
import { RestoreBackup } from './RestoreBackup'

export const PASSWORD_MIN = 8

function scheduleLabel(t: TFunction, value: BackupSchedule): string {
  if (value === 'off') return t('backups.schedule.off')
  if (value === 'daily') return t('backups.schedule.daily')
  if (value === 'weekly') return t('backups.schedule.weekly')
  return t('backups.schedule.monthly')
}

function kindLabel(t: TFunction, kind: BackupKind): string {
  if (kind === 'manual') return t('backups.kind.manual')
  if (kind === 'scheduled') return t('backups.kind.scheduled')
  return t('backups.kind.update')
}

/** Die Notiz einer Kopie: die beiden, die nexcrate selbst schreibt, in der eingestellten Sprache; sonst wie getippt. */
function noteText(t: TFunction, comment: string): string {
  if (comment === 'before restore') return t('backups.autoNote.beforeRestore')
  if (comment === 'before schema change') return t('backups.autoNote.beforeSchemaChange')
  return comment || '·'
}

function size(bytes: number): string {
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MiB`
  return `${Math.max(1, Math.round(bytes / 1024))} KiB`
}

/**
 * System, "Sicherungen": im Aufbau von Nexviews Sicherungsseite. Zeitplan und Anzahl oben, die Liste mit
 * Herunterladen und Loeschen, ganz unten zugeklappt das Wiederherstellen.
 *
 * ⚠️ Der Knopf, auf den es ankommt, ist "Herunterladen": Die Kopien liegen auf derselben Platte wie die Datenbank.
 * Eine Sicherung wird daraus erst, wenn sie den Rechner verlaesst.
 */
export function BackupSettings() {
  const { t, i18n } = useTranslation()
  const notify = useNotice()
  const [list, setList] = useState<BackupList | null>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const [token, setToken] = useState(0)
  const [creating, setCreating] = useState(false)
  const [downloading, setDownloading] = useState<Backup | null>(null)
  const [deleting, setDeleting] = useState<Backup | null>(null)
  const [restoring, setRestoring] = useState<Backup | null>(null)
  const [restoreOpen, setRestoreOpen] = useState(false)

  useEffect(() => {
    let current = true
    backupsApi.list().then(
      (result) => {
        if (!current) return
        setList(result)
        setProblem(null)
      },
      (error: unknown) => {
        if (current) setProblem(error)
      },
    )
    return () => {
      current = false
    }
  }, [token])

  const reload = useCallback(() => setToken((count) => count + 1), [])

  async function save(body: { schedule?: BackupSchedule; keep?: number }) {
    try {
      setList(await backupsApi.settings(body))
      setProblem(null)
      notify(t('backups.saved'))
    } catch (error) {
      setProblem(error)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Section title={t('backups.title')} intro={t('backups.intro')}>
        {list === null ? (
          problem === null && (
            <p className="flex items-center gap-2 py-2 text-sm text-mist-500" role="status">
              <Spinner />
              {t('common.loading')}
            </p>
          )
        ) : (
          <div className="flex flex-wrap items-end gap-4">
            <div className="w-56">
              <SelectField
                label={t('backups.scheduleLabel')}
                value={list.schedule}
                onChange={(event) => void save({ schedule: event.target.value as BackupSchedule })}
              >
                {BACKUP_SCHEDULES.map((value) => (
                  <option key={value} value={value}>
                    {scheduleLabel(t, value)}
                  </option>
                ))}
              </SelectField>
            </div>
            <div className="w-32">
              <Field
                key={list.keep}
                label={t('backups.keepLabel')}
                type="number"
                min={2}
                max={50}
                defaultValue={list.keep}
                onBlur={(event) => {
                  const value = Number(event.target.value)
                  if (Number.isInteger(value) && value >= 2 && value <= 50 && value !== list.keep) void save({ keep: value })
                }}
              />
            </div>
            <p className="max-w-md flex-1 text-xs text-mist-500">{t('backups.keepHint')}</p>
          </div>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </Section>

      {list !== null && (
        <Section
          title={t('backups.listTitle')}
          actions={
            <Button onClick={() => setCreating(true)}>
              <Symbol name="plus" />
              {t('backups.createNow')}
            </Button>
          }
        >
          {list.entries.length === 0 ? (
            <p className="text-sm text-mist-500">{t('backups.empty')}</p>
          ) : (
            // relative: die unsichtbare Ueberschrift (sr-only) bleibt sonst nicht im scrollbaren Rahmen und macht die Seite breiter.
            <div className="relative overflow-x-auto rounded-xl border border-ink-700">
              <table className="w-full min-w-[44rem] text-left text-sm">
                <thead className="border-b border-ink-700 bg-ink-900 text-xs tracking-wide text-mist-600 uppercase">
                  <tr>
                    <th className="px-4 py-3 font-medium">{t('backups.columns.when')}</th>
                    <th className="px-4 py-3 font-medium">{t('backups.columns.kind')}</th>
                    <th className="px-4 py-3 font-medium">{t('backups.columns.note')}</th>
                    <th className="px-4 py-3 font-medium">{t('backups.columns.version')}</th>
                    <th className="px-4 py-3 text-right font-medium">{t('backups.columns.size')}</th>
                    <th className="px-4 py-3">
                      <span className="sr-only">{t('backups.columns.actions')}</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {list.entries.map((entry) => (
                    <tr key={entry.name} className="border-b border-ink-800 last:border-0">
                      <td className="px-4 py-3 whitespace-nowrap text-mist-100">{formatDateTime(entry.created, i18n.language)}</td>
                      <td className="px-4 py-3">
                        <Badge tone={entry.kind === 'manual' ? 'accent' : 'neutral'}>{kindLabel(t, entry.kind)}</Badge>
                      </td>
                      <td className="px-4 py-3 wrap-anywhere text-mist-500">{noteText(t, entry.comment)}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-mist-500">
                        {entry.version || '·'}
                        {/* Sichtbar schon in der Liste: wer eine Sicherung einer neueren Fassung aufhebt, soll es jetzt wissen. */}
                        {!entry.restorable && (
                          <span className="ml-2">
                            <Badge tone="bad">{t(entry.reason === 'backup_newer' ? 'backups.tooNew' : 'backups.unknownVersion')}</Badge>
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right whitespace-nowrap text-mist-500">{size(entry.size)}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-1">
                          {/* Nur, was diese Version einspielen kann: eine neuere Sicherung bietet keinen Knopf an. */}
                          {entry.restorable && (
                            <button
                              type="button"
                              aria-label={t('backups.restoreCopy.action')}
                              title={t('backups.restoreCopy.action')}
                              onClick={() => setRestoring(entry)}
                              className="rounded-full border border-ink-700 p-2 text-mist-500 transition-colors hover:border-accent-600 hover:text-accent-400"
                            >
                              <Symbol name="refresh" className="h-4 w-4" />
                            </button>
                          )}
                          <button
                            type="button"
                            aria-label={t('backups.download')}
                            title={t('backups.download')}
                            onClick={() => setDownloading(entry)}
                            className="rounded-full border border-ink-700 p-2 text-mist-500 transition-colors hover:border-accent-600 hover:text-accent-400"
                          >
                            <Symbol name="download" className="h-4 w-4" />
                          </button>
                          <button
                            type="button"
                            aria-label={t('backups.delete.action')}
                            title={t('backups.delete.action')}
                            onClick={() => setDeleting(entry)}
                            className="rounded-full border border-ink-700 p-2 text-mist-500 transition-colors hover:border-bad-500 hover:text-bad-500"
                          >
                            <Symbol name="trash" className="h-4 w-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="text-xs text-mist-500">{t('backups.folderHint', { folder: list.folder })}</p>
        </Section>
      )}

      {/* Bewusst unten und zugeklappt: der einzige Knopf auf dieser Seite, der etwas ersetzt statt anzulegen. */}
      <Section title={t('restore.title')} intro={t('restore.intro')} className="border-bad-500/30">
        {restoreOpen ? (
          <>
            <RestoreBackup />
            <Button variant="ghost" className="self-start" onClick={() => setRestoreOpen(false)}>
              {t('common.actions.cancel')}
            </Button>
          </>
        ) : (
          <Button variant="ghost" className="self-start" onClick={() => setRestoreOpen(true)}>
            {t('restore.open')}
          </Button>
        )}
      </Section>

      {creating && (
        <CreateDialog
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false)
            notify(t('backups.create.done'))
            reload()
          }}
        />
      )}
      {downloading && <DownloadDialog backup={downloading} onClose={() => setDownloading(null)} />}
      {restoring && <RestoreCopyDialog backup={restoring} onClose={() => setRestoring(null)} />}
      {deleting && (
        <DeleteDialog
          backup={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={() => {
            setDeleting(null)
            notify(t('backups.delete.done'))
            reload()
          }}
        />
      )}
    </div>
  )
}

function CreateDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const { t } = useTranslation()
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function create(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setProblem(null)
    try {
      await backupsApi.create(comment.trim())
      onCreated()
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  return (
    <Dialog open title={t('backups.create.title')} onClose={() => !busy && onClose()}>
      <form className="flex flex-col gap-4" onSubmit={(event) => void create(event)}>
        <p className="text-sm text-mist-400">{t('backups.create.sub')}</p>
        <Field label={t('backups.create.note')} hint={t('backups.create.noteHint')} value={comment} maxLength={200} onChange={(event) => setComment(event.target.value)} autoFocus />
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button type="submit" loading={busy}>
            {t('backups.createNow')}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function DownloadDialog({ backup, onClose }: { backup: Backup; onClose: () => void }) {
  const { t } = useTranslation()
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const fits = password.length >= PASSWORD_MIN && password === repeat

  async function download(event: FormEvent) {
    event.preventDefault()
    if (!fits) return
    setBusy(true)
    setProblem(null)
    try {
      await backupsApi.download(backup, password)
      onClose()
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  return (
    <Dialog open title={t('backups.downloadDialog.title')} onClose={() => !busy && onClose()}>
      <form className="flex flex-col gap-4" onSubmit={(event) => void download(event)}>
        <p className="font-mono text-xs break-all text-mist-500">{backup.name}</p>
        <p className="text-sm text-mist-400">{t('backups.downloadDialog.what')}</p>
        {/* Ein vergessenes Passwort macht die Sicherung wertlos; einen Zweitschluessel gibt es nicht. */}
        <FormMessage>{t('backups.downloadDialog.warning')}</FormMessage>
        <Field label={t('backups.downloadDialog.password')} type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} autoFocus />
        <Field label={t('backups.downloadDialog.repeat')} type="password" autoComplete="new-password" value={repeat} onChange={(event) => setRepeat(event.target.value)} />
        {password.length > 0 && password.length < PASSWORD_MIN && <p className="text-sm text-mist-500">{t('backups.downloadDialog.tooShort', { count: PASSWORD_MIN })}</p>}
        {repeat.length > 0 && password !== repeat && <p className="text-sm text-bad-500">{t('backups.downloadDialog.mismatch')}</p>}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button type="submit" disabled={!fits} loading={busy}>
            {t('backups.download')}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function DeleteDialog({ backup, onClose, onDeleted }: { backup: Backup; onClose: () => void; onDeleted: () => void }) {
  const { t, i18n } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function remove() {
    setBusy(true)
    setProblem(null)
    try {
      await backupsApi.remove(backup.name)
      onDeleted()
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  return (
    <Dialog
      open
      title={t('backups.delete.title')}
      onClose={() => !busy && onClose()}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="danger" loading={busy} onClick={() => void remove()}>
            {t('backups.delete.action')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm text-mist-300">{t('backups.delete.text', { when: formatDateTime(backup.created, i18n.language), note: noteText(t, backup.comment) })}</p>
        <FormMessage>{t('backups.delete.warning')}</FormMessage>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}


/** Eine Kopie der Liste einspielen, ohne Herunterladen. Danach startet nexcrate neu; das Fenster wartet darauf. */
function RestoreCopyDialog({ backup, onClose }: { backup: Backup; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const [restarting, setRestarting] = useState<string | null>(null)

  async function restore() {
    setBusy(true)
    setProblem(null)
    try {
      const before = await systemApi.health()
      await backupsApi.restoreCopy(backup.name)
      setRestarting(before.started)
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  return (
    <Dialog
      open
      title={t('backups.restoreCopy.title')}
      onClose={() => !busy && restarting === null && onClose()}
      footer={
        restarting === null ? (
          <>
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              {t('common.actions.cancel')}
            </Button>
            <Button variant="danger" loading={busy} onClick={() => void restore()}>
              {t('restore.restoreNow')}
            </Button>
          </>
        ) : undefined
      }
    >
      {restarting !== null ? (
        <RestartWaiting started={restarting} />
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-mist-300">
            {t('backups.restoreCopy.text', { when: formatDateTime(backup.created, i18n.language), note: noteText(t, backup.comment) })}
          </p>
          <FormMessage tone="info">{t('restore.outside')}</FormMessage>
          <FormMessage>{t('restore.replaceWarning')}</FormMessage>
          {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
        </div>
      )}
    </Dialog>
  )
}

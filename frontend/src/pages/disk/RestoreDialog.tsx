import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { errorText } from '../../api/client'
import { diskApi } from '../../api/disk'
import type { DiskFolder, DiskJob, DiskRoot, DiskRootKind } from '../../api/types'
import { versionsApi } from '../../api/versions'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, Spinner, Toggle } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatList } from '../../lib/format'
import { SERIES_VERSIONS_TAB_PATH } from '../settings/tabs'
import { useVersions } from '../versions/useVersions'
import { companionLabelsOf, versionLabelsOf } from './diskText'

/**
 * "Wiederherstellen": how many movies come back from their `release.nex` and into which versions. A root's movies go
 * into the root's version; in a folder the owner added the label in the file decides, and a label without a version
 * offers "Fassung {{label}} anlegen". Then `POST /api/disk/restore` starts the job the page follows.
 *
 * Serienordner (S6, Entscheidung 28) kommen ebenso zurueck, Folgen und Angaben aus der release.nex je Staffelordner.
 * Seit 18.09.2026 nennt die Zeile die Fassungen aus den release.nex aller Staffelordner, also bietet der Dialog wie
 * bei Filmen an, eine fehlende anzulegen. Nennt die Datei keine, fuehrt er ohne Serienfassung zu den Fassungen.
 */
export function RestoreDialog({
  count,
  roots,
  folders,
  target,
  onClose,
  onStarted,
  kind = 'movie',
}: {
  /** How many movies the job would restore. */
  count: number
  /** The roots whose movies are restored, for the versions they go into. */
  roots: DiskRoot[]
  /** The restorable rows the page has loaded, for the labels their `release.nex` name. */
  folders: DiskFolder[]
  /** Every restorable row, or the chosen ones. */
  target: { all: true } | { folder_ids: number[] }
  onClose: () => void
  onStarted: (job: DiskJob) => void
  kind?: DiskRootKind
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const notify = useNotice()
  const series = kind === 'series'
  const { versions, error: versionsError, reload } = useVersions(kind)
  const [busy, setBusy] = useState(false)
  const [keep, setKeep] = useState(true)
  const [creating, setCreating] = useState<string | null>(null)
  const [problem, setProblem] = useState<unknown>(null)

  const rootLabels = versionLabelsOf(roots)
  // Labels in the files that no definition has. In a root with a version the label is only the check; without one it decides.
  const fileLabels = companionLabelsOf(folders)
  const known = new Set((versions ?? []).map((version) => version.label))
  const missing = versions === null ? [] : fileLabels.filter((label) => !known.has(label))
  const labels = [...rootLabels, ...fileLabels.filter((label) => !rootLabels.includes(label) && known.has(label))]
  // Eine Serienwurzel ohne Serienfassung: Der Lauf legt die Zeile zurueck ("Ohne Fassung"), bis es eine gibt.
  const withoutVersion = series && missing.length === 0 && roots.some((root) => root.versions.length === 0) && versions !== null && versions.length === 0

  async function create(label: string) {
    if (creating !== null) return
    setCreating(label)
    setProblem(null)
    try {
      await versionsApi.create({ kind, label })
      notify(t('disk.bulk.restore.created', { label }))
      reload()
    } catch (error) {
      setProblem(error)
    } finally {
      setCreating(null)
    }
  }

  async function restore() {
    if (busy) return
    setBusy(true)
    setProblem(null)
    try {
      onStarted(await diskApi.restore({ ...target, keep_as_is: keep }))
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
      title={t('disk.bulk.restore.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void restore()} loading={busy} disabled={count === 0}>
            {t('disk.bulk.restore.submit')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm text-mist-200">
          {kind === 'album' ? t('disk.album.bulk.restoreText', { count }) : series ? t('disk.series.bulk.restoreText', { count }) : t('disk.bulk.restore.text', { count })}
        </p>
        {kind === 'album' && <p className="text-sm text-mist-400">{t('disk.album.bulk.restoreNotInLibrary')}</p>}
        {kind !== 'album' && labels.length > 0 && (
          <p className="text-sm text-mist-300">
            {series
              ? t('disk.series.bulk.restoreVersions', { count: labels.length, labels: formatList(labels, language) })
              : t('disk.bulk.restore.versions', { count: labels.length, labels: formatList(labels, language) })}
          </p>
        )}
        {versionsError !== null && <FormMessage>{errorText(t, versionsError)}</FormMessage>}
        {versions === null && versionsError === null && (
          <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
            <Spinner />
            {t('common.loading')}
          </p>
        )}
        {withoutVersion && (
          <div className="flex flex-col items-start gap-2 rounded-xl border border-dashed border-ink-600 p-3 text-sm">
            <p className="text-mist-300">{t('disk.series.bulk.restoreNoVersion')}</p>
            <Link to={SERIES_VERSIONS_TAB_PATH} onClick={close} className="inline-flex items-center gap-2 font-semibold text-accent-400 hover:underline">
              {t('disk.assign.noVersionsAction')}
              <Symbol name="arrow" />
            </Link>
          </div>
        )}
        {missing.map((label) => (
          <div key={label} className="flex flex-col gap-2 rounded-xl border border-bad-500/40 bg-bad-500/10 p-3 text-sm">
            <p className="flex items-start gap-2 text-bad-500">
              <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
              <span className="min-w-0 wrap-anywhere">{t('disk.bulk.restore.missing', { label })}</span>
            </p>
            <p className="pl-6 text-mist-300">{t('disk.bulk.restore.missingHint')}</p>
            <div className="pl-6">
              <Button size="sm" variant="ghost" onClick={() => void create(label)} loading={creating === label} disabled={creating !== null || busy}>
                <Symbol name="plus" />
                {t('disk.bulk.restore.create', { label })}
              </Button>
            </div>
          </div>
        ))}
        <Toggle label={t('disk.keep.label')} hint={t('disk.keep.hint')} checked={keep} onChange={setKeep} disabled={busy} />
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

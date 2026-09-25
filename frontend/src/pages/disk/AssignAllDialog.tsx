import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { diskApi } from '../../api/disk'
import type { DiskFolder, DiskJob, DiskRoot, DiskRootKind } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, Toggle } from '../../components/ui'
import { formatList } from '../../lib/format'
import { versionLabelsOf } from './diskText'

/**
 * "Alle eindeutigen zuordnen" (decision 18): the loaded proposals whose proposal is unambiguous, counted and with the
 * versions of their roots. With every proposal loaded, `POST /api/disk/assign` gets their ids; with rows not loaded
 * yet it gets `all`, and the server takes every unambiguous proposal, so nobody has to load thousands of rows first.
 *
 * Serienordner (S6) gehen denselben Weg, mit der Regel "Alle Folgen"; danach liest nexcrate jeden Ordner ein.
 */
export function AssignAllDialog({
  total,
  folders,
  roots,
  onClose,
  onStarted,
  kind = 'movie',
}: {
  /** How many proposal rows there are, loaded or not. */
  total: number
  /** The proposal rows the page has loaded. */
  folders: DiskFolder[]
  roots: DiskRoot[]
  onClose: () => void
  onStarted: (job: DiskJob) => void
  kind?: DiskRootKind
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const series = kind === 'series'
  const [busy, setBusy] = useState(false)
  const [keep, setKeep] = useState(true)
  const [problem, setProblem] = useState<unknown>(null)

  // Ein Albumordner (Musik M6) traegt seine Vorschlaege unter `album`; zugeordnet wird nur genau ein eindeutiges Album der Bibliothek.
  const unambiguous = folders.filter((folder) =>
    kind === 'album'
      ? (folder.album?.proposals ?? []).filter((proposal) => proposal.unambiguous && proposal.title_id !== null).length === 1
      : Array.isArray(folder.proposals) && folder.proposals.some((proposal) => proposal.unambiguous === true),
  )
  const rootIds = new Set(unambiguous.map((folder) => folder.root_id))
  const labels = versionLabelsOf(roots.filter((root) => rootIds.has(root.id)))
  const notLoaded = Math.max(0, total - folders.length)

  const canAssign = unambiguous.length > 0 || notLoaded > 0

  async function assign() {
    if (busy || !canAssign) return
    setBusy(true)
    setProblem(null)
    try {
      const target = notLoaded > 0 ? { all: true as const } : { folder_ids: unambiguous.map((folder) => folder.id) }
      onStarted(await diskApi.assignMany({ ...target, keep_as_is: keep }))
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
      title={t('disk.bulk.assign.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void assign()} loading={busy} disabled={!canAssign}>
            {t('disk.bulk.assign.submit')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {unambiguous.length === 0 && notLoaded === 0 ? (
          <p className="text-sm text-mist-200">{t('disk.bulk.assign.none')}</p>
        ) : (
          <>
            <p className="text-sm text-mist-200">{kind === 'album'
                ? t('disk.album.bulk.assignText', { count: unambiguous.length })
                : series
                  ? t('disk.series.bulk.assignText', { count: unambiguous.length })
                  : t('disk.bulk.assign.text', { count: unambiguous.length })}</p>
            {kind !== 'album' && labels.length > 0 && (
              <p className="text-sm text-mist-300">
                {series
                  ? t('disk.series.bulk.assignVersions', { count: labels.length, labels: formatList(labels, language) })
                  : t('disk.bulk.assign.versions', { count: labels.length, labels: formatList(labels, language) })}
              </p>
            )}
            <p className="flex items-start gap-2 text-sm text-mist-400">
              <Symbol name="info" className="mt-0.5 h-4 w-4 shrink-0 text-info-500" />
              <span>{kind === 'album' ? t('disk.album.assign.reading') : series ? t('disk.series.bulk.assignCompanion') : t('disk.bulk.assign.companion')}</span>
            </p>
          </>
        )}
        {notLoaded > 0 && <FormMessage tone="info">{t('disk.bulk.assign.notLoaded', { count: notLoaded })}</FormMessage>}
        <Toggle label={t('disk.keep.label')} hint={t('disk.keep.hint')} checked={keep} onChange={setKeep} disabled={busy} />
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

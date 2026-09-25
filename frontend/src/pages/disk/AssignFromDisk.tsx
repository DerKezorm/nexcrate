import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { diskApi } from '../../api/disk'
import type { DiskFolder, DiskFolderState, DiskRoot, OnDiskFolder, TitleDetail, TmdbResult } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, FormMessage, Spinner } from '../../components/ui'
import { AssignDialog } from './AssignDialog'
import { pickedFromResult } from './pickedMovie'

type Loaded = { folder: DiskFolder; root: DiskRoot | null }

/**
 * "Zuordnen …" from the add dialog (L8): the search result names only the folder's id, root and name, so the row and
 * its root are fetched first, then the assign dialog opens with the movie chosen. The row is found through the list
 * filtered by its state and name; a row the scan no longer has says so.
 */
export function AssignFromDisk({ entry, movie, onClose, onAssigned }: { entry: OnDiskFolder; movie: TmdbResult; onClose: () => void; onAssigned: (detail: TitleDetail) => void }) {
  const { t } = useTranslation()
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [gone, setGone] = useState(false)

  useEffect(() => {
    let current = true
    const state = (typeof entry.state === 'string' ? entry.state : 'unknown') as DiskFolderState
    Promise.all([diskApi.overview(), diskApi.folders({ state, rootId: null, q: entry.name, offset: 0, limit: 50 })]).then(
      ([overview, list]) => {
        if (!current) return
        const folder = list.items.find((item) => item.id === entry.folder_id) ?? null
        if (folder === null) {
          setGone(true)
          return
        }
        setLoaded({ folder, root: overview.roots.find((root) => root.id === folder.root_id) ?? null })
      },
      (problem: unknown) => {
        if (current) setError(problem)
      },
    )
    return () => {
      current = false
    }
  }, [entry.folder_id, entry.name, entry.state])

  if (loaded !== null) return <AssignDialog folder={loaded.folder} root={loaded.root} movie={pickedFromResult(movie)} onClose={onClose} onAssigned={onAssigned} />

  return (
    <Dialog
      open
      title={t('disk.assign.title', { name: entry.name })}
      onClose={onClose}
      footer={
        <Button variant="ghost" onClick={onClose}>
          {t('common.actions.close')}
        </Button>
      }
    >
      {error !== null ? (
        <FormMessage>{errorText(t, error)}</FormMessage>
      ) : gone ? (
        <FormMessage tone="info">{t('disk.assign.loadFailed')}</FormMessage>
      ) : (
        <p className="flex items-center gap-2 py-2 text-sm text-mist-500" role="status">
          <Spinner />
          {t('common.loading')}
        </p>
      )}
    </Dialog>
  )
}

import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { errorText } from '../../api/client'
import { diskApi } from '../../api/disk'
import { musicApi } from '../../api/music'
import type { DiskFolder, DiskFolderState, DiskJob, DiskOverview, DiskRoot, DiskRootKind, Proposal } from '../../api/types'
import { Segmented } from '../../components/Segmented'
import { Symbol } from '../../components/Symbol'
import { TabRow, type Tab } from '../../components/TabRow'
import { Button, FormMessage, PageLoading, PageTitle } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { FolderPicker } from '../files/FolderPicker'
import { KIND_PARAM, TMDB_TAB_PATH } from '../settings/tabs'
import { useKindLabel } from '../settings/useKindLabel'
import { AlbumAssignDialog } from './AlbumAssignDialog'
import { AssignAllDialog } from './AssignAllDialog'
import { AssignDialog } from './AssignDialog'
import {
  addressOfDiskKind,
  countOf,
  DISK_KINDS,
  diskKindFromAddress,
  initialTab,
  rootsOfKind,
  stateLabel,
  statesOf,
  tabCount,
  tabsOf,
  type DiskTab,
} from './diskText'
import { FolderGroup } from './FolderGroup'
import type { RowActions } from './FolderRow'
import { JobNote } from './JobNote'
import { pickedFromProposal, type PickedMovie } from './pickedMovie'
import { RestoreDialog } from './RestoreDialog'
import { RootList } from './RootList'
import { SeriesAssignDialog } from './SeriesAssignDialog'
import { useDiskJob } from './useDiskJob'
import type { FolderListing } from './useFolderList'

/** Gesucht wird erst, wenn so lange nichts getippt wurde. */
export const DISK_SEARCH_DELAY_MS = 300

type DialogState =
  | { kind: 'assign'; folder: DiskFolder; movie: PickedMovie | null }
  | { kind: 'assignSeries'; folder: DiskFolder }
  | { kind: 'assignAlbum'; folder: DiskFolder }
  | { kind: 'restore'; target: { all: true } | { folder_ids: number[] }; count: number }
  | { kind: 'assignAll' }
  | { kind: 'picker' }
  | null

/**
 * "Ordner einlesen" (L5): the scanned folders with their counts, the running job, and the folders the scan found by
 * state, with assigning, restoring and ignoring per row and for many at once. The page opens on the first of
 * "Wiederherstellen", "Vorschläge" and "Unbekannt" that is not empty. nexcrate moves and deletes nothing here.
 *
 * Ein Umschalter waehlt Filme oder Serien (S6, Entscheidung 25); die Art steht als `art` in der Adresse. Beide Seiten
 * zeigen ihre eigenen Wurzeln, Zaehler, Reiter und Zeilen, und "Ordner hinzufuegen" legt eine Wurzel der Art an.
 */
export function DiskPage() {
  const { t } = useTranslation()
  const notify = useNotice()
  const navigate = useNavigate()
  const kindLabel = useKindLabel()
  const [params, setParams] = useSearchParams()
  const kind = diskKindFromAddress(params.get(KIND_PARAM))
  const [overview, setOverview] = useState<DiskOverview | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [retries, setRetries] = useState(0)
  const [tab, setTab] = useState<DiskTab | null>(null)
  const [rootId, setRootId] = useState<number | null>(null)
  const [searchInput, setSearchInput] = useState('')
  const [q, setQ] = useState('')
  const [version, setVersion] = useState(0)
  const [dialog, setDialog] = useState<DialogState>(null)
  const [listings, setListings] = useState<Partial<Record<DiskFolderState, FolderListing | null>>>({})

  const reload = useCallback(() => {
    diskApi.overview().then(
      (result) => setOverview(result),
      () => undefined,
    )
  }, [])

  // A job ended: the roots carry new counts, and the rows may have moved between the tabs.
  const finished = useCallback(() => {
    reload()
    setVersion((count) => count + 1)
  }, [reload])
  const { job, lost, error: jobError, follow, dismiss } = useDiskJob(overview?.job ?? null, finished)
  const busy = job !== null && job.state === 'running'

  useEffect(() => {
    let current = true
    const abort = new AbortController()
    setError(null)
    diskApi.overview(abort.signal).then(
      (result) => {
        if (!current) return
        setOverview(result)
      },
      (problem: unknown) => {
        if (current && !abort.signal.aborted) setError(problem)
      },
    )
    return () => {
      current = false
      abort.abort()
    }
  }, [retries])

  // Die andere Art hat eigene Wurzeln und eigene Zeilen: Reiter, Filter und geladene Listen fangen neu an.
  useEffect(() => {
    setTab(null)
    setRootId(null)
    setListings({})
    setDialog(null)
  }, [kind])

  // Der erste Reiter steht erst fest, wenn die Zahlen der Wurzeln da sind.
  useEffect(() => {
    if (overview === null) return
    setTab((chosen) => chosen ?? initialTab(rootsOfKind(overview.roots, kind), kind))
  }, [overview, kind])

  // Typed at once into the field; sent to the server after a pause.
  useEffect(() => {
    const next = searchInput.trim()
    if (next === q) return
    const timer = window.setTimeout(() => setQ(next), DISK_SEARCH_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [searchInput, q])

  const onListing = useCallback((state: DiskFolderState, listing: FolderListing | null) => {
    setListings((current) => (current[state] === listing ? current : { ...current, [state]: listing }))
  }, [])

  const roots = overview === null ? [] : rootsOfKind(overview.roots, kind)
  const restorableRoots = roots.filter((root) => (root.counts?.restorable ?? 0) > 0)

  function changeKind(next: DiskRootKind) {
    const nextParams = new URLSearchParams(params)
    if (next === 'movie') nextParams.delete(KIND_PARAM)
    else nextParams.set(KIND_PARAM, addressOfDiskKind(next))
    setParams(nextParams, { replace: true })
  }

  function fail(problem: unknown) {
    notify(errorText(t, problem))
  }

  async function startJob(start: () => Promise<DiskJob>, started: string | null) {
    try {
      const accepted = await start()
      follow(accepted)
      if (started !== null) notify(started)
      return true
    } catch (problem) {
      fail(problem)
      return false
    }
  }

  const actions: RowActions = {
    onAssign: (folder: DiskFolder, proposal: Proposal | null) =>
      setDialog(
        kind === 'series'
          ? { kind: 'assignSeries', folder }
          : kind === 'album'
            ? { kind: 'assignAlbum', folder }
            : { kind: 'assign', folder, movie: proposal ? pickedFromProposal(proposal) : null },
      ),
    onRestore: async (folder: DiskFolder) => {
      // Ein Album aus einer release.nex, das noch nicht in der Bibliothek steht, holt nexcrate zuerst von MusicBrainz.
      const missing = kind === 'album' ? folder.album?.proposals.find((proposal) => proposal.title_id === null && proposal.mbid !== null) : undefined
      if (missing?.mbid) {
        try {
          await musicApi.addAlbum({ mbid: missing.mbid })
        } catch (problem) {
          fail(problem)
          return
        }
      }
      setDialog({ kind: 'restore', target: { folder_ids: [folder.id] }, count: 1 })
    },
    onTakePath: async (folder: DiskFolder) => {
      try {
        await diskApi.takePath(folder.id)
        notify(t('disk.row.pathTaken', { name: folder.relative_path }))
        reload()
        setVersion((count) => count + 1)
      } catch (problem) {
        fail(problem)
      }
    },
    onIgnore: async (folder: DiskFolder) => {
      try {
        await diskApi.ignore(folder.id)
        notify(t('disk.row.ignored', { name: folder.relative_path }))
        reload()
        setVersion((count) => count + 1)
      } catch (problem) {
        fail(problem)
      }
    },
    onUnignore: async (folder: DiskFolder) => {
      try {
        await diskApi.unignore(folder.id)
        notify(t('disk.row.unignored', { name: folder.relative_path }))
        reload()
        setVersion((count) => count + 1)
      } catch (problem) {
        fail(problem)
      }
    },
  }

  function assigned(folder: DiskFolder) {
    setDialog(null)
    notify(t('disk.assign.done', { name: folder.relative_path }))
    reload()
    setVersion((count) => count + 1)
  }

  // Eine zugeordnete Serie liest nexcrate jetzt ein; wie es laeuft, steht auf ihrer Seite.
  function assignedSeries(folder: DiskFolder, titleId: number) {
    setDialog(null)
    notify(t('disk.series.assign.done', { name: folder.relative_path }))
    navigate(`/titel/${titleId}`)
  }

  // Ein zugeordnetes Album hat nexcrate schon eingelesen; was unklar blieb, steht auf seiner Seite.
  function assignedAlbum(folder: DiskFolder) {
    setDialog(null)
    notify(t('disk.album.assign.done', { name: folder.relative_path }))
    reload()
    setVersion((count) => count + 1)
  }

  async function removeRoot(root: DiskRoot) {
    try {
      await diskApi.removeRoot(root.id)
      notify(t('disk.roots.removed', { path: root.path }))
      reload()
      setVersion((count) => count + 1)
    } catch (problem) {
      fail(problem)
    }
  }

  if (error !== null && overview === null) {
    return (
      <div className="flex flex-col items-start gap-4">
        <BackLink />
        <FormMessage>{errorText(t, error)}</FormMessage>
        <Button variant="ghost" onClick={() => setRetries((count) => count + 1)}>
          <Symbol name="refresh" />
          {t('common.actions.retry')}
        </Button>
      </div>
    )
  }

  if (overview === null || tab === null) return <PageLoading />

  const series = kind === 'series'
  const music = kind === 'album'
  const tabs: Tab<DiskTab>[] = tabsOf(kind).map((value) => ({ value, label: stateLabel(t, value), count: tabCount(roots, value, rootId, kind) }))
  const restorable = countOf(roots, 'restorable', rootId)
  const proposals = countOf(roots, 'proposal', rootId)
  const states = statesOf(kind, tab)
  const restorableRows = listings.restorable?.items ?? []
  const proposalRows = listings.proposal?.items ?? []
  const restoreDialog = dialog?.kind === 'restore' ? dialog : null

  return (
    <div className="flex flex-col gap-8">
      <BackLink />
      <PageTitle sub={music ? t('disk.album.sub') : series ? t('disk.series.sub') : t('disk.sub')}>{t('disk.title')}</PageTitle>

      <Segmented value={kind} options={DISK_KINDS} onChange={changeKind} label={(option) => kindLabel(option === 'album' ? 'music' : option)} ariaLabel={t('disk.kindLabel')} />

      {!overview.tmdb_ready && !music && (
        <FormMessage tone="info">
          {t('disk.noTmdb')}{' '}
          <Link to={TMDB_TAB_PATH} className="font-medium text-accent-400 hover:underline">
            {t('disk.noTmdbLink')}
          </Link>
        </FormMessage>
      )}

      <JobNote job={job} lost={lost} error={jobError} onDismiss={dismiss} kind={kind} />

      <RootList
        roots={roots}
        busy={busy}
        kind={kind}
        onScan={(root) => void startJob(() => diskApi.scan(root ? [root.id] : roots.map((entry) => entry.id)), null)}
        onAdd={() => setDialog({ kind: 'picker' })}
        onRemove={(root) => void removeRoot(root)}
      />

      <section className="flex flex-col gap-4" aria-label={t('disk.tabs.label')}>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <TabRow label={t('disk.tabs.label')} tabs={tabs} active={tab} onChange={setTab} />
          <div className="flex shrink-0 flex-wrap gap-2">
            {restorable > 0 && (
              <Button size="sm" onClick={() => setDialog({ kind: 'restore', target: { all: true }, count: restorable })} disabled={busy}>
                <Symbol name="check" />
                {t('disk.bulk.restoreAll')}
              </Button>
            )}
            {proposals > 0 && (
              <Button size="sm" variant={restorable > 0 ? 'ghost' : 'primary'} onClick={() => setDialog({ kind: 'assignAll' })} disabled={busy}>
                <Symbol name="layers" />
                {t('disk.bulk.assignAll')}
              </Button>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="relative w-full sm:max-w-sm">
            <Symbol name="search" className="pointer-events-none absolute top-1/2 left-3.5 h-4 w-4 -translate-y-1/2 text-mist-600" />
            <input
              type="search"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder={t('disk.filter.search')}
              aria-label={t('disk.filter.search')}
              className="w-full rounded-full border border-ink-700 bg-ink-900 py-2 pr-4 pl-10 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
            />
          </div>
          {roots.length > 1 && (
            <label className="flex min-w-0 items-center gap-2 text-sm text-mist-500">
              {t('disk.filter.root')}
              <select
                value={rootId === null ? '' : String(rootId)}
                onChange={(event) => setRootId(event.target.value === '' ? null : Number(event.target.value))}
                className="min-w-0 max-w-full rounded-full border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
              >
                <option value="">{t('disk.filter.allRoots')}</option>
                {roots.map((root) => (
                  <option key={root.id} value={String(root.id)}>
                    {root.path}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>

        <div className="flex flex-col gap-6">
          {states.map((state) => (
            <FolderGroup
              key={state}
              state={state}
              rootId={rootId}
              q={q}
              version={version}
              heading={states.length > 1}
              actions={actions}
              busy={busy}
              onListing={onListing}
              kind={kind}
            />
          ))}
        </div>
      </section>

      {dialog?.kind === 'assign' && (
        <AssignDialog
          folder={dialog.folder}
          root={roots.find((root) => root.id === dialog.folder.root_id) ?? null}
          movie={dialog.movie}
          onClose={() => setDialog(null)}
          onAssigned={() => assigned(dialog.folder)}
        />
      )}
      {dialog?.kind === 'assignSeries' && (
        <SeriesAssignDialog
          folder={dialog.folder}
          root={roots.find((root) => root.id === dialog.folder.root_id) ?? null}
          onClose={() => setDialog(null)}
          onAssigned={(detail) => assignedSeries(dialog.folder, detail.id)}
        />
      )}
      {dialog?.kind === 'assignAlbum' && <AlbumAssignDialog folder={dialog.folder} onClose={() => setDialog(null)} onAssigned={() => assignedAlbum(dialog.folder)} />}
      {restoreDialog !== null && (
        <RestoreDialog
          count={restoreDialog.count}
          roots={restorableRoots}
          folders={'folder_ids' in restoreDialog.target ? restorableRows.filter((row) => (restoreDialog.target as { folder_ids: number[] }).folder_ids.includes(row.id)) : restorableRows}
          target={restoreDialog.target}
          kind={kind}
          onClose={() => setDialog(null)}
          onStarted={(started) => {
            setDialog(null)
            follow(started)
            notify(music ? t('disk.album.bulk.restoreStarted') : series ? t('disk.series.bulk.restoreStarted') : t('disk.bulk.restore.started'))
          }}
        />
      )}
      {dialog?.kind === 'assignAll' && (
        <AssignAllDialog
          total={proposals}
          folders={proposalRows}
          roots={roots}
          kind={kind}
          onClose={() => setDialog(null)}
          onStarted={(started) => {
            setDialog(null)
            follow(started)
            notify(music ? t('disk.album.bulk.assignStarted') : series ? t('disk.series.bulk.assignStarted') : t('disk.bulk.assign.started'))
          }}
        />
      )}
      {dialog?.kind === 'picker' && (
        <FolderPicker
          title={music ? t('disk.album.roots.pickerTitle') : series ? t('disk.series.roots.pickerTitle') : t('disk.roots.pickerTitle')}
          hint={music ? t('disk.album.roots.pickerHint') : series ? t('disk.series.roots.pickerHint') : t('disk.roots.pickerHint')}
          start={null}
          onClose={() => setDialog(null)}
          onTake={async (path) => {
            const root = await diskApi.addRoot(path, kind)
            setDialog(null)
            notify(t('disk.roots.added', { path: root.path }))
            reload()
          }}
        />
      )}
    </div>
  )
}

function BackLink() {
  const { t } = useTranslation()
  return (
    <Link to="/" className="inline-flex w-fit items-center gap-2 text-sm text-mist-500 hover:text-mist-100">
      <Symbol name="back" />
      {t('disk.backToLibrary')}
    </Link>
  )
}

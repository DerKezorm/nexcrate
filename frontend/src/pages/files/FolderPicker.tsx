import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import { foldersApi } from '../../api/folders'
import type { FolderListing, FolderMount } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, Spinner } from '../../components/ui'
import { sizeText } from '../../lib/size'
import { SpaceLine } from './SpaceLine'

function Loading() {
  const { t } = useTranslation()
  return (
    <p className="flex items-center gap-2 py-2 text-sm text-mist-500" role="status">
      <Spinner />
      {t('common.loading')}
    </p>
  )
}

/** Ein Ordner als Knopf zum Oeffnen. Lange Namen brechen um. */
function FolderButton({ name, detail, mono = false, onClick }: { name: string; detail?: string; mono?: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full min-w-0 items-center gap-3 rounded-xl border border-ink-700 bg-ink-900 px-3.5 py-2.5 text-left text-sm transition-colors hover:border-accent-500/60"
    >
      <Symbol name="folder" className="h-4 w-4 shrink-0 text-accent-400" />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className={'wrap-anywhere text-mist-100 ' + (mono ? 'font-mono' : '')}>{name}</span>
        {detail && <span className="text-xs text-mist-500">{detail}</span>}
      </span>
      <Symbol name="chevron" className="h-4 w-4 shrink-0 text-mist-500" />
    </button>
  )
}

/**
 * Ein Ordner, gewaehlt durch Blaettern: der Standardordner einer Fassung, oder bei der Uebernahme der Ordner, unter dem
 * nexcrate einen Stammordner von Radarr sieht. ⚠️ Kein Textfeld: Wer einen Pfad eintippt, tippt einen, den nexcrate
 * nicht sieht (Befund 4 vom 13.09.2026). Es geht los bei den eingebundenen Ordnern, oder bei `start`, wenn nexcrate
 * ihn noch sieht. Nehmen laesst sich jeder geoeffnete Ordner. `onTake` schliesst das Fenster; wirft es, steht der
 * Fehler hier, etwa wenn der Server den Ordner einer Fassung ablehnt.
 */
export function FolderPicker({
  title,
  hint,
  warning,
  start,
  onClose,
  onTake,
}: {
  title: string
  /** Ein Satz oben im Fenster, etwa was im Standardordner einer Fassung landet. */
  hint?: string
  /** Eine Warnung zum geoeffneten Ordner, oder null. Sperrt nichts. */
  warning?: (path: string) => string | null
  start: string | null
  onClose: () => void
  onTake: (path: string) => Promise<void> | void
}) {
  const { t, i18n } = useTranslation()
  const startFolder = start
  // null: die eingebundenen Ordner. Sonst der geoeffnete Ordner.
  const [path, setPath] = useState<string | null>(startFolder)
  const [listing, setListing] = useState<FolderListing | null>(null)
  const [mounts, setMounts] = useState<FolderMount[] | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const takeReasonId = useId()
  const atMounts = path === null

  useEffect(() => {
    let current = true
    setLoadError(null)
    setListing(null)
    if (path === null) {
      foldersApi.mounts().then(
        (result) => {
          if (current) setMounts(Array.isArray(result.mounts) ? result.mounts : [])
        },
        (error: unknown) => {
          if (current) setLoadError(error)
        },
      )
    } else {
      foldersApi.list(path).then(
        (result) => {
          if (current) setListing(result)
        },
        (error: unknown) => {
          if (!current) return
          // Den gespeicherten Ordner sieht nexcrate nicht mehr, etwa nach einem Umbau des Containers: dann von oben.
          if (path === startFolder && error instanceof ApiError && error.code === 'folder_not_visible') setPath(null)
          else setLoadError(error)
        },
      )
    }
    return () => {
      current = false
    }
  }, [path, startFolder])

  // Wer einen Ordner oeffnet, verliert den Knopf unter dem Fokus. Dann steht der Fokus auf der Ueberschrift, nicht im Nichts.
  useEffect(() => {
    const active = document.activeElement
    if (active === null || active === document.body) headingRef.current?.focus()
  }, [path, listing, mounts])

  function open(next: string | null) {
    if (busy) return
    setProblem(null)
    setPath(next)
  }

  async function take() {
    if (listing === null || busy) return
    setBusy(true)
    setProblem(null)
    try {
      await onTake(listing.path)
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  function close() {
    if (!busy) onClose()
  }

  const spaceOf = (mount: FolderMount) =>
    t('settings.files.folders.space', { free: sizeText(t, mount.free_bytes, i18n.language), total: sizeText(t, mount.total_bytes, i18n.language) })

  return (
    <Dialog
      open
      wide
      title={title}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void take()} loading={busy} disabled={listing === null} aria-describedby={atMounts ? takeReasonId : undefined}>
            {t('settings.files.picker.take')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {hint && <p className="text-sm text-mist-400">{hint}</p>}
        <div className="flex flex-col gap-2">
          <h3 ref={headingRef} tabIndex={-1} className={'text-sm outline-none ' + (atMounts ? 'font-semibold text-mist-200' : 'font-mono break-all text-mist-100')}>
            {atMounts ? t('settings.files.picker.mounts') : t('settings.files.picker.current', { path: listing?.path ?? path ?? '' })}
          </h3>
          {!atMounts && listing !== null && warning?.(listing.path) && (
            <p role="note" className="flex items-start gap-2 text-sm text-bad-400">
              <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{warning(listing.path)}</span>
            </p>
          )}
          {!atMounts && (
            <div>
              <Button variant="ghost" size="sm" onClick={() => open(listing?.parent ?? null)} disabled={busy}>
                <Symbol name="back" />
                {listing?.parent ? t('settings.files.picker.up') : t('settings.files.picker.toMounts')}
              </Button>
            </div>
          )}
        </div>

        {loadError !== null ? (
          <FormMessage>{errorText(t, loadError)}</FormMessage>
        ) : atMounts ? (
          mounts === null ? (
            <Loading />
          ) : mounts.length === 0 ? (
            <p className="text-sm text-mist-500">{t('settings.files.folders.noMounts')}</p>
          ) : (
            <ul aria-label={t('settings.files.picker.mounts')} className="flex flex-col gap-1.5">
              {mounts.map((mount) => (
                <li key={mount.path} className="min-w-0">
                  <FolderButton name={mount.path} mono detail={spaceOf(mount)} onClick={() => open(mount.path)} />
                </li>
              ))}
            </ul>
          )
        ) : listing === null ? (
          <Loading />
        ) : (
          <>
            <SpaceLine path={listing.path} freeBytes={listing.free_bytes} totalBytes={listing.total_bytes} />
            {listing.folders.length === 0 ? (
              <p className="text-sm text-mist-500">{t('settings.files.picker.empty')}</p>
            ) : (
              <ul aria-label={t('settings.files.picker.listLabel', { path: listing.path })} className="flex flex-col gap-1.5">
                {listing.folders.map((folder) => (
                  <li key={folder.path} className="min-w-0">
                    <FolderButton name={folder.name} onClick={() => open(folder.path)} />
                  </li>
                ))}
              </ul>
            )}
          </>
        )}

        {atMounts && (
          <p id={takeReasonId} className="text-xs text-mist-500">
            {t('settings.files.picker.takeBlocked')}
          </p>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

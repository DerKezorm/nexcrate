import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import type { DiskFolderState, DiskRootKind } from '../../api/types'
import { Button, FormMessage, Spinner } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { stateLabel } from './diskText'
import { FolderRow, type RowActions } from './FolderRow'
import { useFolderList, type FolderListing } from './useFolderList'

/**
 * The rows of one state with their count and "Mehr laden". A tab with several states stacks groups, each with its
 * heading; a tab with one state shows the group without one. The page learns what is loaded through `onListing`, for
 * the dialogs that count and name what they act on.
 */
export function FolderGroup({
  state,
  rootId,
  q,
  version,
  heading,
  actions,
  busy,
  onListing,
  kind = 'movie',
}: {
  state: DiskFolderState
  rootId: number | null
  q: string
  /** Counts up when the rows should be read again, for example after a job ended. */
  version: number
  heading: boolean
  actions: RowActions
  busy: boolean
  onListing?: (state: DiskFolderState, listing: FolderListing | null) => void
  /** Filmordner oder Serienordner (S6). */
  kind?: DiskRootKind
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const { listing, loading, error, loadMore, hasMore } = useFolderList(state, rootId, q, version, kind)

  useEffect(() => {
    onListing?.(state, listing)
  }, [state, listing, onListing])

  if (listing === null && error === null) {
    return (
      <p className="flex items-center gap-2 py-4 text-sm text-mist-500" role="status">
        <Spinner />
        {t('common.loading')}
      </p>
    )
  }

  // A secondary group with nothing in it stays away: "Disc-Ordner: nichts" would only be noise.
  if (heading && listing !== null && listing.total === 0 && error === null) return null

  return (
    <section aria-label={stateLabel(t, state)} className="flex flex-col gap-3" aria-busy={loading === 'first'}>
      {heading && <h3 className="text-sm font-semibold text-mist-300">{stateLabel(t, state)}</h3>}
      {error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
      {listing !== null && listing.total === 0 ? (
        <p className="rounded-xl border border-dashed border-ink-700 px-4 py-6 text-center text-sm text-mist-500">{q.trim() !== '' ? t('disk.list.emptySearch') : t('disk.list.empty')}</p>
      ) : (
        listing !== null && (
          <>
            <p className="flex items-center gap-2 text-xs text-mist-500" role="status">
              {t('disk.list.count', { count: listing.total, value: formatNumber(listing.total, language) })}
              {loading === 'first' && <Spinner className="h-3.5 w-3.5" />}
            </p>
            <ul className={'flex flex-col gap-2 ' + (loading === 'first' ? 'opacity-60' : '')}>
              {listing.items.map((folder) => (
                <li key={folder.id} className="min-w-0">
                  <FolderRow folder={folder} actions={actions} busy={busy} kind={kind} />
                </li>
              ))}
            </ul>
            {hasMore && (
              <div className="flex flex-col items-center gap-1.5">
                <Button variant="ghost" size="sm" onClick={() => void loadMore()} loading={loading === 'more'} disabled={loading !== null}>
                  {t('disk.list.more')}
                </Button>
                <p className="text-xs text-mist-500 tabular-nums">{t('disk.list.shown', { shown: formatNumber(listing.items.length, language), total: formatNumber(listing.total, language) })}</p>
              </div>
            )}
          </>
        )
      )}
    </section>
  )
}

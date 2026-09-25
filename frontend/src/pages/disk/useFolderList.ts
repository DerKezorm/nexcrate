import { useCallback, useEffect, useRef, useState } from 'react'

import { DISK_PAGE_SIZE, diskApi } from '../../api/disk'
import type { DiskFolder, DiskFolderState, DiskRootKind } from '../../api/types'

export type FolderListing = { items: DiskFolder[]; total: number }

/**
 * The rows of one state, page by page. A new state, root, search text or kind starts over; "Mehr laden" appends the
 * next page. `refresh` reloads what is shown from the start, for example after a job ended or a row changed. Only the
 * newest request counts: a slow old answer never overwrites a newer one.
 */
export function useFolderList(state: DiskFolderState, rootId: number | null, q: string, version: number, kind: DiskRootKind = 'movie') {
  const [listing, setListing] = useState<FolderListing | null>(null)
  const [loading, setLoading] = useState<'first' | 'more' | null>('first')
  const [error, setError] = useState<unknown>(null)
  const generation = useRef(0)

  useEffect(() => {
    const run = ++generation.current
    const abort = new AbortController()
    setLoading('first')
    setError(null)
    diskApi.folders({ state, rootId, q, offset: 0, kind }, abort.signal).then(
      (result) => {
        if (run !== generation.current) return
        setListing({ items: Array.isArray(result.items) ? result.items : [], total: result.total })
        setLoading(null)
      },
      (problem: unknown) => {
        if (run !== generation.current || abort.signal.aborted) return
        setError(problem)
        setLoading(null)
      },
    )
    return () => abort.abort()
  }, [state, rootId, q, version, kind])

  const loadMore = useCallback(async () => {
    if (listing === null || loading !== null) return
    const run = generation.current
    setLoading('more')
    setError(null)
    try {
      const result = await diskApi.folders({ state, rootId, q, offset: listing.items.length, kind })
      if (run !== generation.current) return
      setListing((previous) => {
        if (previous === null) return previous
        const known = new Set(previous.items.map((item) => item.id))
        return { items: [...previous.items, ...result.items.filter((item) => !known.has(item.id))], total: result.total }
      })
    } catch (problem) {
      if (run === generation.current) setError(problem)
    } finally {
      if (run === generation.current) setLoading(null)
    }
  }, [listing, loading, state, rootId, q, kind])

  /** A row changed (ignored, assigned, restored): it leaves this list at once, the count follows. */
  const drop = useCallback((id: number) => {
    setListing((previous) => {
      if (previous === null || !previous.items.some((item) => item.id === id)) return previous
      return { items: previous.items.filter((item) => item.id !== id), total: Math.max(0, previous.total - 1) }
    })
  }, [])

  // The server may have more rows than the page shows. An empty page with a total above zero still offers to load.
  const hasMore = listing !== null && listing.items.length < listing.total

  return { listing, loading, error, loadMore, drop, hasMore, pageSize: DISK_PAGE_SIZE }
}

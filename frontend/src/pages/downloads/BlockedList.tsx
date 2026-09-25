import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { blocklistApi } from '../../api/blocklist'
import { errorText } from '../../api/client'
import type { BlocklistPage, BlocklistRow } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatDate, formatNumber } from '../../lib/format'
import { blockReasonText } from '../title/blocklistText'

/**
 * Der Reiter "Sperrliste": jedes gesperrte Release mit Titel, Indexer, Grund und Datum, je mit "Sperre aufheben"
 * (Rueckmeldung 20.09.2026). Ein fehlgeschlagener Download sperrt sein Release, ohne zu fragen; hier ist das zu
 * sehen. Die Sperrliste eines einzelnen Titels steht weiter auf seiner Seite.
 */
export function BlockedList() {
  const { t, i18n } = useTranslation()
  const notify = useNotice()
  const [page, setPage] = useState(1)
  const [listing, setListing] = useState<BlocklistPage | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [token, setToken] = useState(0)

  useEffect(() => {
    const abort = new AbortController()
    blocklistApi.all(page, abort.signal).then(
      (result) => {
        if (!abort.signal.aborted) {
          setListing(result)
          setError(null)
        }
      },
      (problem: unknown) => {
        if (!abort.signal.aborted) setError(problem)
      },
    )
    return () => abort.abort()
  }, [page, token])

  const removed = useCallback(
    (row: BlocklistRow) => {
      notify(t('downloads.blocklist.lifted', { title: row.release_title }))
      setToken((count) => count + 1)
    },
    [notify, t],
  )

  if (error !== null) return <FormMessage>{errorText(t, error)}</FormMessage>
  if (listing === null)
    return (
      <p className="flex items-center gap-2 py-4 text-sm text-mist-500" role="status">
        <Spinner />
        {t('common.loading')}
      </p>
    )
  if (listing.items.length === 0) return <p className="py-4 text-sm text-mist-500">{t('downloads.blocklist.empty')}</p>

  const pages = Math.max(1, Math.ceil(listing.total / Math.max(1, listing.per_page)))
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-mist-500">{t('downloads.blocklist.intro')}</p>
      <ul aria-label={t('downloads.tabs.blocklist')} className="flex flex-col overflow-hidden rounded-2xl border border-ink-700 bg-ink-850/60">
        {listing.items.map((row) => (
          <BlockedRow key={row.id} row={row} onRemoved={() => removed(row)} />
        ))}
      </ul>
      {pages > 1 && (
        <nav aria-label={t('downloads.pages.label')} className="flex flex-wrap items-center gap-3">
          <Button variant="ghost" size="sm" disabled={listing.page <= 1} onClick={() => setPage(listing.page - 1)}>
            <Symbol name="back" />
            {t('downloads.pages.previous')}
          </Button>
          <span className="text-sm text-mist-500 tabular-nums">
            {t('downloads.pages.position', { page: formatNumber(listing.page, i18n.language), pages: formatNumber(pages, i18n.language) })}
          </span>
          <Button variant="ghost" size="sm" disabled={listing.page >= pages} onClick={() => setPage(listing.page + 1)}>
            {t('downloads.pages.next')}
            <Symbol name="arrow" />
          </Button>
        </nav>
      )}
    </div>
  )
}

function BlockedRow({ row, onRemoved }: { row: BlocklistRow; onRemoved: () => void }) {
  const { t, i18n } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function lift() {
    setBusy(true)
    setProblem(null)
    try {
      await blocklistApi.remove(row.id)
      onRemoved()
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  const title = row.title
  return (
    <li className="flex min-w-0 flex-col gap-2 border-b border-ink-700/60 px-4 py-3 last:border-b-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 flex-col gap-0.5">
        <p className="font-mono text-xs leading-5 wrap-anywhere text-mist-200">{row.release_title}</p>
        <p className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-mist-500">
          {title !== null ? (
            <Link to={`/titel/${title.id}`} className="text-mist-300 hover:text-accent-400">
              {title.year !== null ? `${title.title} (${title.year})` : title.title}
            </Link>
          ) : (
            <span>{t('downloads.blocklist.titleGone')}</span>
          )}
          <span>{row.indexer}</span>
          <span>{blockReasonText(t, row.reason)}</span>
          <span>{formatDate(row.created_at, i18n.language)}</span>
        </p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
      <Button variant="ghost" size="sm" loading={busy} onClick={() => void lift()} aria-label={t('downloads.blocklist.liftLabel', { title: row.release_title })}>
        {t('downloads.blocklist.lift')}
      </Button>
    </li>
  )
}

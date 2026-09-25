import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { errorText } from '../../api/client'
import type { WaitingPage, WaitingRelease } from '../../api/types'
import { waitingApi } from '../../api/waiting'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatDateTime, formatNumber } from '../../lib/format'
import { sizeText } from '../../lib/size'
import { VERSIONS_TAB_PATH } from '../settings/tabs'
import { protocolName } from '../versions/delayText'

/**
 * Der Reiter "Wartet": Releases, die die Automatik behaelt statt sie zu laden, weil die Verzoegerungsregel der Fassung
 * auf ein besseres warten laesst. Je Zeile: ab wann es laden darf, "Jetzt laden" und
 * "Verwerfen". Ist die Zeit um, laedt das beste bekannte Release von selbst.
 */
export function WaitingList() {
  const { t, i18n } = useTranslation()
  const notify = useNotice()
  const [page, setPage] = useState(1)
  const [listing, setListing] = useState<WaitingPage | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [token, setToken] = useState(0)
  // Wann die Liste kam: daran misst jede Zeile, ob ihre Zeit um ist.
  const [readAt, setReadAt] = useState(0)

  useEffect(() => {
    const abort = new AbortController()
    waitingApi.list(page, abort.signal).then(
      (result) => {
        if (!abort.signal.aborted) {
          setReadAt(Date.now())
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

  const changed = useCallback(
    (message: string) => {
      notify(message)
      setToken((count) => count + 1)
    },
    [notify],
  )

  if (error !== null) return <FormMessage>{errorText(t, error)}</FormMessage>
  if (listing === null)
    return (
      <p className="flex items-center gap-2 py-4 text-sm text-mist-500" role="status">
        <Spinner />
        {t('common.loading')}
      </p>
    )
  if (listing.items.length === 0)
    return (
      <div className="flex flex-col gap-2 py-4 text-sm text-mist-500">
        <p>{t('downloads.waiting.empty')}</p>
        <p>
          {t('downloads.waiting.emptyHint')}{' '}
          <Link to={VERSIONS_TAB_PATH} className="text-accent-400 hover:underline">
            {t('downloads.waiting.toVersions')}
          </Link>
        </p>
      </div>
    )

  const pages = Math.max(1, Math.ceil(listing.total / Math.max(1, listing.per_page)))
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-mist-500">{t('downloads.waiting.intro')}</p>
      <ul aria-label={t('downloads.tabs.waiting')} className="flex flex-col overflow-hidden rounded-2xl border border-ink-700 bg-ink-850/60">
        {listing.items.map((row) => (
          <WaitingRow key={row.id} row={row} readAt={readAt} onChanged={changed} />
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

function WaitingRow({ row, readAt, onChanged }: { row: WaitingRelease; readAt: number; onChanged: (message: string) => void }) {
  const { t, i18n } = useTranslation()
  const [busy, setBusy] = useState<'load' | 'discard' | null>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const due = new Date(row.due_at).getTime() <= readAt

  async function run(action: 'load' | 'discard') {
    setBusy(action)
    setProblem(null)
    try {
      if (action === 'load') {
        await waitingApi.load(row.id)
        onChanged(t('downloads.waiting.loaded', { title: row.release_title }))
      } else {
        await waitingApi.discard(row.id)
        onChanged(t('downloads.waiting.discarded', { title: row.release_title }))
      }
    } catch (error) {
      setProblem(error)
      setBusy(null)
    }
  }

  const title = row.title
  return (
    <li className="flex min-w-0 flex-col gap-2 border-b border-ink-700/60 px-4 py-3 last:border-b-0 lg:flex-row lg:items-center lg:justify-between">
      <div className="flex min-w-0 flex-col gap-1">
        <p className="font-mono text-xs leading-5 wrap-anywhere text-mist-200">{row.release_title}</p>
        <p className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-mist-500">
          <Link to={`/titel/${title.id}`} className="text-mist-300 hover:text-accent-400">
            {title.year !== null ? `${title.title} (${title.year})` : title.title}
          </Link>
          <span>{row.version}</span>
          {row.episodes.length > 0 && <span>{row.episodes.join(', ')}</span>}
          <span>{row.indexer}</span>
          <span>{protocolName(t, row.protocol)}</span>
          {row.quality !== null && <span>{row.quality}</span>}
          {row.size_bytes !== null && <span>{sizeText(t, row.size_bytes, i18n.language)}</span>}
        </p>
        <p className="flex flex-wrap items-center gap-2 text-sm text-mist-300">
          <Badge tone={due ? 'accent' : 'neutral'}>
            <Symbol name="clock" className="h-3.5 w-3.5" />
            {!due
              ? t('downloads.waiting.until', { date: formatDateTime(row.due_at, i18n.language) })
              : row.automatic_on === false
                ? t('downloads.waiting.dueButOff')
                : t('downloads.waiting.due')}
          </Badge>
        </p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
      <div className="flex shrink-0 flex-wrap gap-1.5">
        <Button size="sm" loading={busy === 'load'} disabled={busy !== null} onClick={() => void run('load')} aria-label={t('downloads.waiting.loadLabel', { title: row.release_title })}>
          <Symbol name="download" className="h-3.5 w-3.5" />
          {t('downloads.waiting.load')}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          loading={busy === 'discard'}
          disabled={busy !== null}
          onClick={() => void run('discard')}
          aria-label={t('downloads.waiting.discardLabel', { title: row.release_title })}
        >
          {t('downloads.waiting.discard')}
        </Button>
      </div>
    </li>
  )
}

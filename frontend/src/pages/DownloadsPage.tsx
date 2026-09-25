import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'

import { errorText } from '../api/client'
import type { DownloadClient, DownloadList, DownloadsTab, DownloadsView } from '../api/types'
import { Symbol } from '../components/Symbol'
import { TabRow, type Tab } from '../components/TabRow'
import { Button, FormMessage, PageTitle, Spinner } from '../components/ui'
import { useNotice } from '../components/useNotice'
import { formatNumber } from '../lib/format'
import { clientErrorText } from './clients/clientText'
import { ActiveList } from './downloads/ActiveList'
import { BlockedList } from './downloads/BlockedList'
import { addressOfView, viewFromAddress } from './downloads/address'
import { DownloadHistory } from './downloads/DownloadHistory'
import { ProblemList } from './downloads/ProblemList'
import { useDownloadList } from './downloads/useDownloadList'
import { WaitingList } from './downloads/WaitingList'
import { CLIENTS_TAB_PATH } from './settings/tabs'

/**
 * Downloads mit vier Reitern: Aktiv, Probleme, Verlauf und seit dem 20.09.2026 die Sperrliste. Keine Statuswoerter der
 * Programme: Jedes Problem sagt in einem Satz, was los ist, und bietet den naechsten Schritt an. Der
 * Reiter steht in der Adresse (`?reiter=probleme`), damit die Titelseite genau dorthin fuehren kann.
 * Die Seite fragt alle 5 Sekunden nach, solange sie offen ist.
 */
export function DownloadsPage() {
  const { t } = useTranslation()
  const notify = useNotice()
  const [params, setParams] = useSearchParams()
  const tab = viewFromAddress(params.get('reiter'))
  // Die Sperrliste und die Wartenden holen sich ihre Zeilen selbst; die Downloads werden dafuer nicht abgefragt.
  const own = tab === 'blocklist' || tab === 'waiting'
  const view: DownloadsView = tab === 'blocklist' || tab === 'waiting' ? 'active' : tab
  const [paging, setPaging] = useState<{ view: DownloadsView; page: number }>({ view, page: 1 })
  const page = paging.view === view ? paging.page : 1
  const { list, clients, foreign, error, reload } = useDownloadList(view, page)

  const show = useCallback((next: DownloadsTab) => setParams({ reiter: addressOfView(next) }, { replace: true }), [setParams])

  const changed = useCallback(
    (message?: string) => {
      if (message) notify(message)
      reload()
    },
    [notify, reload],
  )

  // Nach dem Entfernen des letzten Eintrags einer hinteren Seite: auf die letzte Seite, die es noch gibt.
  useEffect(() => {
    if (list !== null && list.items.length === 0 && page > 1) setPaging({ view, page: Math.max(1, Math.ceil(list.total / list.per_page)) })
  }, [list, page, view])

  const counts = list?.counts
  const tabs: Tab<DownloadsTab>[] = [
    { value: 'active', label: t('downloads.tabs.active'), symbol: 'download', count: counts?.active },
    // Seit dem 22.09.2026 zaehlen die Auftraege mit, die nexcrate nicht bestellt hat: auch sie brauchen dich.
    { value: 'problems', label: t('downloads.tabs.problems'), symbol: 'alert', count: counts === undefined ? undefined : counts.needs_owner + foreign.length },
    { value: 'waiting', label: t('downloads.tabs.waiting'), symbol: 'clock' },
    { value: 'history', label: t('downloads.tabs.history'), symbol: 'clock' },
    { value: 'blocklist', label: t('downloads.tabs.blocklist'), symbol: 'close' },
  ]
  const failing = (clients ?? []).filter((client) => client.enabled && client.last_error_code !== null)

  return (
    <div className="flex flex-col gap-8">
      <PageTitle sub={t('downloads.sub')}>{t('downloads.title')}</PageTitle>

      <div className="flex flex-col gap-5">
        <TabRow tabs={tabs} active={tab} onChange={show} label={t('downloads.tabs.label')} />
        {failing.map((client) => (
          <ClientNote key={client.id} client={client} />
        ))}
        {!own && error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
        {tab === 'blocklist' ? (
          <BlockedList />
        ) : tab === 'waiting' ? (
          <WaitingList />
        ) : list === null ? (
          error === null && (
            <p className="flex items-center gap-2 py-4 text-sm text-mist-500" role="status">
              <Spinner />
              {t('common.loading')}
            </p>
          )
        ) : view === 'active' ? (
          <ActiveList items={list.items} needsOwner={list.counts.needs_owner + foreign.length} clients={clients} onShowProblems={() => show('problems')} onChanged={changed} />
        ) : view === 'problems' ? (
          <ProblemList items={list.items} foreign={foreign} onChanged={changed} />
        ) : (
          <DownloadHistory items={list.items} />
        )}
        {!own && list !== null && <Pager list={list} onPage={(next) => setPaging({ view, page: next })} />}
      </div>
    </div>
  )
}

/** Ein eingeschaltetes Programm, das zuletzt einen Fehler hatte. Seine Downloads bleiben, wie sie sind. */
function ClientNote({ client }: { client: DownloadClient }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-bad-500/40 bg-bad-500/10 px-3.5 py-2.5 text-sm sm:flex-row sm:items-center sm:justify-between">
      <p className="flex min-w-0 items-start gap-2 text-mist-200">
        <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0 text-bad-500" />
        <span className="min-w-0 wrap-anywhere">{t('downloads.clients.failing', { name: client.name, text: clientErrorText(t, client.last_error_code ?? '') })}</span>
      </p>
      <Link to={CLIENTS_TAB_PATH} className="shrink-0 font-medium text-accent-400 hover:underline">
        {t('downloads.clients.link')}
      </Link>
    </div>
  )
}

function Pager({ list, onPage }: { list: DownloadList; onPage: (page: number) => void }) {
  const { t, i18n } = useTranslation()
  const pages = Math.max(1, Math.ceil(list.total / Math.max(1, list.per_page)))
  if (pages <= 1) return null
  return (
    <nav aria-label={t('downloads.pages.label')} className="flex flex-wrap items-center gap-3">
      <Button variant="ghost" size="sm" disabled={list.page <= 1} onClick={() => onPage(list.page - 1)}>
        <Symbol name="back" />
        {t('downloads.pages.previous')}
      </Button>
      <span className="text-sm text-mist-500 tabular-nums">
        {t('downloads.pages.position', { page: formatNumber(list.page, i18n.language), pages: formatNumber(pages, i18n.language) })}
      </span>
      <Button variant="ghost" size="sm" disabled={list.page >= pages} onClick={() => onPage(list.page + 1)}>
        {t('downloads.pages.next')}
        <Symbol name="arrow" />
      </Button>
    </nav>
  )
}

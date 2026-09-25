import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { Download, DownloadClient } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button, ProgressBar, Section } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { sizeText } from '../../lib/size'
import { percentOf } from '../../lib/states'
import { CLIENTS_TAB_PATH } from '../settings/tabs'
import { DownloadHead } from './DownloadHead'
import { clientNameOf, isRunning, problemReasonText, problemWhyText, remainingText } from './downloadText'
import { RemoveDownloadDialog } from './RemoveDownloadDialog'

/**
 * Was gerade laedt, bis es abgelegt ist. Oben ein knapper Hinweis, wenn Downloads den Besitzer
 * brauchen, der zum Reiter Probleme fuehrt. Ohne Download-Programm sagt die leere Liste, was fehlt.
 */
export function ActiveList({
  items,
  needsOwner,
  clients,
  onShowProblems,
  onChanged,
}: {
  items: Download[]
  needsOwner: number
  clients: DownloadClient[] | null
  onShowProblems: () => void
  onChanged: (message?: string) => void
}) {
  const { t, i18n } = useTranslation()
  const [removing, setRemoving] = useState<Download | null>(null)

  return (
    <div className="flex flex-col gap-6">
      {needsOwner > 0 && (
        <div className="flex flex-col gap-3 rounded-2xl border border-bad-500/40 bg-bad-500/10 p-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
          <div className="flex min-w-0 items-start gap-3">
            <Symbol name="alert" className="mt-0.5 h-5 w-5 shrink-0 text-bad-500" />
            <div className="flex min-w-0 flex-col gap-0.5">
              <p className="font-semibold text-mist-100">{t('downloads.summary.title', { count: needsOwner, value: formatNumber(needsOwner, i18n.language) })}</p>
              <p className="text-sm text-mist-400">{t('downloads.summary.text')}</p>
            </div>
          </div>
          <Button variant="danger" size="sm" className="w-fit shrink-0" onClick={onShowProblems}>
            {t('downloads.summary.open')}
            <Symbol name="arrow" />
          </Button>
        </div>
      )}

      <Section title={t('downloads.active.title')} intro={t('downloads.active.intro')}>
        {items.length === 0 ? (
          clients !== null && clients.length === 0 ? (
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-mist-500">
              <span>{t('downloads.clients.none')}</span>
              <Link to={CLIENTS_TAB_PATH} className="font-medium text-accent-400 hover:underline">
                {t('downloads.clients.noneLink')}
              </Link>
            </p>
          ) : (
            <p className="text-sm text-mist-500">{t('downloads.active.empty')}</p>
          )
        ) : (
          <ul className="flex flex-col">
            {items.map((download) => (
              <ActiveRow key={download.id} download={download} onRemove={() => setRemoving(download)} />
            ))}
          </ul>
        )}
      </Section>

      {removing && (
        <RemoveDownloadDialog
          download={removing}
          blocklist={false}
          onClose={() => setRemoving(null)}
          onRemoved={() => {
            setRemoving(null)
            onChanged(t('downloads.remove.done'))
          }}
        />
      )}
    </div>
  )
}

function ActiveRow({ download, onRemove }: { download: Download; onRemove: () => void }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const title = download.title.title
  const running = isRunning(download.state)
  const percent = running && download.progress !== null ? percentOf(download.progress) : null
  const facts = [
    percent !== null ? t('downloads.active.percent', { value: formatNumber(percent, language) }) : null,
    download.release.size_bytes !== null ? sizeText(t, download.release.size_bytes, language) : null,
    download.state === 'downloading' && download.remaining_seconds !== null && download.remaining_seconds > 0
      ? t('downloads.active.left', { time: remainingText(t, download.remaining_seconds, language) })
      : null,
  ].filter((fact): fact is string => fact !== null)
  const problem = download.problem
  const client = clientNameOf(t, download)

  return (
    <li className="flex min-w-0 flex-col gap-2.5 border-t border-ink-700/60 py-4 first:border-t-0 first:pt-0">
      <DownloadHead download={download}>
        <Button variant="ghost" size="sm" onClick={onRemove} aria-label={t('downloads.actions.removeLabel', { title })}>
          <Symbol name="trash" />
          {t('downloads.actions.remove')}
        </Button>
      </DownloadHead>
      {percent !== null && <ProgressBar value={percent / 100} label={t('downloads.active.progress', { title })} />}
      {facts.length > 0 && (
        <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-mist-500 tabular-nums">
          {facts.map((fact) => (
            <span key={fact}>{fact}</span>
          ))}
        </p>
      )}
      {problem !== null && (
        <p className={'flex items-start gap-2 text-sm ' + (problem.needs_owner ? 'text-bad-500' : 'text-mist-300')}>
          <Symbol name={problem.needs_owner ? 'alert' : 'info'} className={'mt-0.5 h-4 w-4 shrink-0 ' + (problem.needs_owner ? '' : 'text-info-500')} />
          <span className="min-w-0 wrap-anywhere">
            {problemReasonText(t, problem.code, client)} {problemWhyText(t, problem, client)}
          </span>
        </p>
      )}
      <p className="font-mono text-xs leading-5 wrap-anywhere text-mist-600">{download.release.title}</p>
    </li>
  )
}

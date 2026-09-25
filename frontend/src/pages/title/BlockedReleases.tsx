import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { blocklistApi } from '../../api/blocklist'
import { errorText } from '../../api/client'
import type { BlocklistEntry } from '../../api/types'
import { Button, FormMessage, Section } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatDate } from '../../lib/format'
import { blockReasonText } from './blocklistText'

/**
 * "Gesperrte Releases" auf der Titelseite: Release, Indexer, Grund und Datum, je mit "Sperre aufheben". Ohne
 * Eintrag steht hier nichts. Die letzte aufgehobene Sperre nimmt den Abschnitt mit.
 */
export function BlockedReleases({ entries, onRemoved }: { entries: BlocklistEntry[]; onRemoved: (id: number) => void }) {
  const { t } = useTranslation()
  if (entries.length === 0) return null

  return (
    <Section title={t('title.blocklist.title')} intro={t('title.blocklist.intro')}>
      <ul aria-label={t('title.blocklist.title')} className="flex flex-col">
        {entries.map((entry) => (
          <BlockedRow key={entry.id} entry={entry} onRemoved={() => onRemoved(entry.id)} />
        ))}
      </ul>
    </Section>
  )
}

function BlockedRow({ entry, onRemoved }: { entry: BlocklistEntry; onRemoved: () => void }) {
  const { t, i18n } = useTranslation()
  const notify = useNotice()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function lift() {
    setBusy(true)
    setProblem(null)
    try {
      await blocklistApi.remove(entry.id)
      notify(t('title.blocklist.removed'))
      onRemoved()
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  return (
    <li className="flex min-w-0 flex-col gap-2 border-t border-ink-700/60 py-3 first:border-t-0 first:pt-0 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
      <div className="flex min-w-0 flex-col gap-1">
        <p className="font-mono text-xs leading-5 wrap-anywhere text-mist-200">{entry.release_title}</p>
        <p className="text-sm text-mist-300">{blockReasonText(t, entry.reason)}</p>
        <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-mist-500">
          <span className="min-w-0 wrap-anywhere">{entry.indexer}</span>
          <time dateTime={entry.created_at}>{t('title.blocklist.since', { date: formatDate(entry.created_at, i18n.language) })}</time>
        </p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
      <div className="shrink-0">
        <Button variant="ghost" size="sm" loading={busy} onClick={() => void lift()} aria-label={t('title.blocklist.removeLabel', { title: entry.release_title })}>
          {t('title.blocklist.remove')}
        </Button>
      </div>
    </li>
  )
}

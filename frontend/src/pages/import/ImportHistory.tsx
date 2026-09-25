import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { importsApi } from '../../api/imports'
import type { ImportRun, ImportStatus, Source } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Section, Spinner } from '../../components/ui'
import { formatDateTime } from '../../lib/format'
import { runCounts, runErrorText } from './runText'

function StatusBadge({ status }: { status: ImportStatus }) {
  const { t } = useTranslation()
  if (status === 'running') return <Badge tone="info">{t('import.history.running')}</Badge>
  if (status === 'done') return <Badge tone="ok">{t('import.history.done')}</Badge>
  return <Badge tone="bad">{t('import.history.failed')}</Badge>
}

/** Die letzten Laeufe, auch die, die der Server alle 15 Minuten selbst startet. */
export function ImportHistory({ sources, refreshToken }: { sources: Source[]; refreshToken: number }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [runs, setRuns] = useState<ImportRun[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [manual, setManual] = useState(0)

  useEffect(() => {
    let current = true
    importsApi.list().then(
      (result) => {
        if (!current) return
        setRuns(result)
        setError(null)
      },
      (problem: unknown) => {
        if (current) setError(problem)
      },
    )
    return () => {
      current = false
    }
  }, [refreshToken, manual])

  return (
    <Section
      title={t('import.history.title')}
      intro={t('import.history.intro')}
      actions={
        <Button variant="ghost" size="sm" onClick={() => setManual((count) => count + 1)}>
          <Symbol name="refresh" className="h-3.5 w-3.5" />
          {t('import.history.refresh')}
        </Button>
      }
    >
      {error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
      {runs === null ? (
        error === null && (
          <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
            <Spinner />
            {t('common.loading')}
          </p>
        )
      ) : runs.length === 0 ? (
        <p className="text-sm text-mist-500">{t('import.history.empty')}</p>
      ) : (
        <ol className="flex flex-col">
          {runs.map((run) => {
            const source = sources.find((candidate) => candidate.id === run.source_id)
            const detail = run.status === 'failed' ? runErrorText(t, run, source) : run.status === 'done' ? runCounts(t, run, language) : t('import.run.running')
            return (
              <li key={run.id} className="flex flex-col gap-1 border-t border-ink-700/60 py-3 first:border-t-0 first:pt-0 sm:flex-row sm:items-start sm:gap-4">
                <div className="flex min-w-0 flex-1 flex-col gap-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium wrap-anywhere text-mist-100">{source?.name ?? t('import.history.removedSource')}</span>
                    <StatusBadge status={run.status} />
                  </div>
                  <p className={'text-sm wrap-anywhere ' + (run.status === 'failed' ? 'text-bad-500' : 'text-mist-400')}>{detail}</p>
                </div>
                <time dateTime={run.started_at} className="shrink-0 text-xs text-mist-500 tabular-nums">
                  {formatDateTime(run.started_at, language)}
                </time>
              </li>
            )
          })}
        </ol>
      )}
    </Section>
  )
}

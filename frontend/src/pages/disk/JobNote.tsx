import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import type { DiskJob, DiskRootKind } from '../../api/types'
import { Button, FormMessage, ProgressBar, Spinner } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { jobDoneText, jobFailedText, jobOutcomes, jobTitle, phaseText } from './diskText'

/**
 * The newest job: while it runs its phase and progress with the hint that the page may be left; when it is done its
 * counts; when it failed the reason. A finished job can be hidden.
 *
 * Auf der Seite der Serien heissen Wiederherstellen und Zuordnen nach Serien (S6).
 */
export function JobNote({ job, lost, error, onDismiss, kind = 'movie' }: { job: DiskJob | null; lost: boolean; error: unknown; onDismiss: () => void; kind?: DiskRootKind }) {
  const { t, i18n } = useTranslation()
  const number = (value: number) => formatNumber(value, i18n.language)
  const title = job === null ? '' : jobTitle(t, job, kind)

  if (job === null) {
    return lost ? <FormMessage tone="info">{t('disk.job.lost')}</FormMessage> : null
  }

  if (job.state === 'running') {
    const progress = job.progress
    return (
      <section aria-label={title} className="flex flex-col gap-3 rounded-2xl border border-info-500/30 bg-info-500/5 p-4">
        <h2 className="text-sm font-semibold text-mist-100">{title}</h2>
        <p className="flex items-center gap-2 text-sm text-info-500" role="status">
          <Spinner />
          {phaseText(t, job.phase)}
        </p>
        {progress !== null && progress.total > 0 && (
          <div className="flex flex-col gap-1.5">
            <ProgressBar value={progress.done / progress.total} label={t('disk.job.progressLabel')} />
            <p className="text-xs text-mist-500 tabular-nums">{t('disk.job.progress', { done: number(progress.done), total: number(progress.total) })}</p>
          </div>
        )}
        <p className="text-xs text-mist-500">{t('disk.job.hint')}</p>
        {error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
      </section>
    )
  }

  const outcomes = jobOutcomes(t, job)
  const failed = job.state === 'failed'
  const conflicts = outcomes.some((row) => (row.key === 'conflict' || row.key === 'conflicts') && row.value > 0)
  return (
    <section aria-label={title} className={'flex flex-col gap-3 rounded-2xl border p-4 ' + (failed ? 'border-bad-500/40 bg-bad-500/10' : 'border-ok-500/40 bg-ok-500/5')}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className={'text-sm font-semibold ' + (failed ? 'text-bad-500' : 'text-ok-500')}>{failed ? jobFailedText(t, job, kind) : jobDoneText(t, job, kind)}</p>
        <Button variant="ghost" size="sm" onClick={onDismiss}>
          {t('disk.job.dismiss')}
        </Button>
      </div>
      {failed && job.error_code !== null && <p className="text-sm text-mist-200">{errorText(t, new ApiError(200, job.error_code))}</p>}
      {outcomes.length > 0 && (
        <dl className="flex flex-wrap gap-1.5">
          {outcomes.map((row) => (
            <div key={row.key} className="inline-flex items-center gap-1.5 rounded-full border border-ink-700 bg-ink-850 px-2.5 py-0.5 text-xs">
              <dt className="text-mist-500">{row.label}</dt>
              <dd className="font-semibold text-mist-100 tabular-nums">{number(row.value)}</dd>
            </div>
          ))}
        </dl>
      )}
      {conflicts && <p className="text-xs text-mist-400">{t('disk.job.conflictsHint')}</p>}
    </section>
  )
}

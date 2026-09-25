import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError, errorText } from '../../api/client'
import { COMPANION_POLL_MS, companionsApi } from '../../api/companions'
import type { CompanionExample, CompanionJob } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, ProgressBar, Section, Spinner, Switch } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatDateTime, formatNumber } from '../../lib/format'
import { allCurrent, companionCountRows, companionCountText, REPLACEABLE } from './companionText'
import { Loading } from './PatternFields'

/** Up to this many example folders per state, as the server sends them. */
const EXAMPLES_SHOWN = 20

/**
 * "Begleitdateien" (L3): the switch "Begleitdateien schreiben", "Prüfen" that writes nothing and "Nachtragen" that
 * writes missing and outdated `release.nex`, the newest report with its counts and example folders, and "Ersetzen"
 * for a file nexcrate would never overwrite on its own, which moves the old file into the recycle folder first.
 */
export function CompanionSection() {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const notify = useNotice()
  const backfillReasonId = useId()
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [switching, setSwitching] = useState(false)
  const [switchProblem, setSwitchProblem] = useState<unknown>(null)
  const [job, setJob] = useState<CompanionJob | null>(null)
  const [starting, setStarting] = useState<'check' | 'backfill' | null>(null)
  const [startProblem, setStartProblem] = useState<unknown>(null)
  const [pollError, setPollError] = useState<unknown>(null)
  const [retry, setRetry] = useState(0)
  const [replacing, setReplacing] = useState<CompanionExample | null>(null)

  useEffect(() => {
    let current = true
    companionsApi.get().then(
      (state) => {
        if (!current) return
        setEnabled(state.enabled === true)
        setJob(state.job ?? null)
      },
      (error: unknown) => {
        if (current) setLoadError(error)
      },
    )
    return () => {
      current = false
    }
  }, [])

  // A running job is asked again every second, each time after the previous answer came.
  useEffect(() => {
    if (job === null || job.state !== 'running') return
    let current = true
    const timer = window.setTimeout(() => {
      companionsApi.job().then(
        (next) => {
          if (!current) return
          setPollError(null)
          setJob(next)
        },
        (problem: unknown) => {
          if (!current) return
          if (problem instanceof ApiError && problem.status === 404) {
            setJob(null)
            return
          }
          setPollError(problem)
          setRetry((count) => count + 1)
        },
      )
    }, COMPANION_POLL_MS)
    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [job, retry])

  async function change(next: boolean) {
    setSwitching(true)
    setSwitchProblem(null)
    try {
      const saved = await companionsApi.save(next)
      setEnabled(saved.enabled === true)
      notify(saved.enabled ? t('settings.files.companions.savedOn') : t('settings.files.companions.savedOff'))
    } catch (error) {
      setSwitchProblem(error)
    } finally {
      setSwitching(false)
    }
  }

  async function start(kind: 'check' | 'backfill') {
    if (starting !== null) return
    setStarting(kind)
    setStartProblem(null)
    try {
      setJob(kind === 'check' ? await companionsApi.check() : await companionsApi.backfill())
    } catch (error) {
      // Another job runs already: the section follows that one.
      if (error instanceof ApiError && error.code === 'companion_job_running') {
        try {
          setJob(await companionsApi.job())
        } catch {
          // Then the first error stands.
        }
      }
      setStartProblem(error)
    } finally {
      setStarting(null)
    }
  }

  const running = job !== null && job.state === 'running'
  const backfillBlocked = enabled === false

  return (
    <Section title={t('settings.files.companions.title')} intro={t('settings.files.companions.intro')}>
      {enabled === null ? (
        loadError !== null ? (
          <FormMessage>{errorText(t, loadError)}</FormMessage>
        ) : (
          <Loading />
        )
      ) : (
        <div className="flex flex-col gap-4">
          <Switch label={t('settings.files.companions.switch')} hint={t('settings.files.companions.hint')} checked={enabled} onChange={(next) => void change(next)} disabled={switching} />
          {!enabled && (
            <p className="flex items-start gap-2 text-sm text-mist-300">
              <Symbol name="info" className="mt-0.5 h-4 w-4 shrink-0 text-info-500" />
              <span>{t('settings.files.companions.off')}</span>
            </p>
          )}
          {switchProblem !== null && <FormMessage>{errorText(t, switchProblem)}</FormMessage>}

          <div className="flex flex-col gap-1.5 border-t border-ink-700 pt-4">
            <div className="flex flex-wrap gap-2">
              <Button variant="ghost" size="sm" onClick={() => void start('check')} loading={starting === 'check'} disabled={starting !== null || running}>
                <Symbol name="search" />
                {t('settings.files.companions.check')}
              </Button>
              <Button
                size="sm"
                onClick={() => void start('backfill')}
                loading={starting === 'backfill'}
                disabled={starting !== null || running || backfillBlocked}
                aria-describedby={backfillBlocked ? backfillReasonId : undefined}
              >
                <Symbol name="check" />
                {t('settings.files.companions.backfill')}
              </Button>
            </div>
            <p className="text-xs text-mist-500">
              {t('settings.files.companions.checkHint')} {t('settings.files.companions.backfillHint')}
            </p>
            {backfillBlocked && (
              <p id={backfillReasonId} className="text-xs text-mist-500">
                {t('settings.files.companions.backfillOff')}
              </p>
            )}
            {startProblem !== null && <FormMessage>{errorText(t, startProblem)}</FormMessage>}
          </div>

          {job !== null && (
            <CompanionReport
              job={job}
              pollError={pollError}
              language={language}
              onReplace={setReplacing}
            />
          )}
        </div>
      )}

      {replacing !== null && (
        <ReplaceDialog
          example={replacing}
          onClose={() => setReplacing(null)}
          onReplaced={(state) => {
            setReplacing(null)
            notify(
              state === 'written'
                ? t('settings.files.companions.replaced', { folder: replacing.folder })
                : t('settings.files.companions.replaceStopped', { folder: replacing.folder, state }),
            )
          }}
        />
      )}
    </Section>
  )
}

function CompanionReport({ job, pollError, language, onReplace }: { job: CompanionJob; pollError: unknown; language: string; onReplace: (example: CompanionExample) => void }) {
  const { t } = useTranslation()
  const number = (value: number) => formatNumber(value, language)

  if (job.state === 'running') {
    const progress = job.progress
    return (
      <section aria-label={job.kind === 'backfill' ? t('settings.files.companions.running.backfill') : t('settings.files.companions.running.check')} className="flex flex-col gap-2 rounded-xl border border-info-500/30 bg-info-500/5 p-3">
        <h3 className="text-sm font-semibold text-mist-100">{job.kind === 'backfill' ? t('settings.files.companions.running.backfill') : t('settings.files.companions.running.check')}</h3>
        {progress !== null && progress.total > 0 ? (
          <>
            <ProgressBar value={progress.done / progress.total} label={t('settings.files.companions.running.progressLabel')} />
            <p className="text-xs text-mist-500 tabular-nums">{t('settings.files.companions.running.progress', { done: number(progress.done), total: number(progress.total) })}</p>
          </>
        ) : (
          <p className="flex items-center gap-2 text-sm text-info-500" role="status">
            <Spinner />
            {t('common.loading')}
          </p>
        )}
        <p className="text-xs text-mist-500">{t('settings.files.companions.running.hint')}</p>
        {pollError !== null && <FormMessage>{errorText(t, pollError)}</FormMessage>}
      </section>
    )
  }

  if (job.state === 'failed') {
    return (
      <div className="flex flex-col gap-2">
        <FormMessage>{job.kind === 'backfill' ? t('settings.files.companions.failed.backfill') : t('settings.files.companions.failed.check')}</FormMessage>
        {job.error_code !== null && <p className="text-sm text-mist-300">{errorText(t, new ApiError(200, job.error_code))}</p>}
      </div>
    )
  }

  const result = job.result
  const rows = companionCountRows(result?.counts)
  const examples = result?.examples && typeof result.examples === 'object' ? result.examples : {}
  const time = formatDateTime(job.finished_at ?? job.started_at, language)
  const heading = job.kind === 'backfill' ? t('settings.files.companions.report.backfill', { time }) : t('settings.files.companions.report.check', { time })

  return (
    <section aria-label={heading} className="flex flex-col gap-3 border-t border-ink-700 pt-4">
      <div>
        <h3 className="text-sm font-semibold text-mist-100">{heading}</h3>
        {result && typeof result.versions === 'number' && <p className="text-xs text-mist-500">{t('settings.files.companions.report.versions', { count: result.versions, value: number(result.versions) })}</p>}
      </div>
      {rows.length === 0 || allCurrent(job) ? (
        <p className="flex items-center gap-2 text-sm text-ok-500">
          <Symbol name="check" className="h-4 w-4 shrink-0" />
          {t('settings.files.companions.report.allCurrent')}
        </p>
      ) : (
        <ul className="flex flex-col gap-1 text-sm text-mist-200">
          {rows.map((row) => (
            <li key={row.state} className={row.state === 'not_writable' ? 'text-bad-500' : ''}>
              {companionCountText(t, row.state, row.count, language)}
            </li>
          ))}
        </ul>
      )}
      {(result?.counts?.not_writable ?? 0) > 0 && (
        <p className="flex items-start gap-2 rounded-xl border border-bad-500/40 bg-bad-500/10 px-3.5 py-2.5 text-sm text-mist-100">
          <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0 text-bad-500" />
          <span className="min-w-0">{t('settings.files.companions.notWritable')}</span>
        </p>
      )}
      {rows
        .filter((row) => Array.isArray(examples[row.state]) && (examples[row.state] as CompanionExample[]).length > 0)
        .map((row) => {
          const list = (examples[row.state] as CompanionExample[]).slice(0, EXAMPLES_SHOWN)
          const more = Math.max(0, row.count - list.length)
          const label = companionCountText(t, row.state, row.count, language)
          return (
            <div key={row.state} className="flex flex-col gap-1.5">
              <h4 className="text-xs font-semibold text-mist-400">
                {t('settings.files.companions.report.examples')}: {label}
              </h4>
              <ul aria-label={label} className="flex flex-col gap-1">
                {list.map((example) => (
                  <li key={`${row.state}-${example.version_id}`} className="flex min-w-0 flex-wrap items-center justify-between gap-2 rounded-lg border border-ink-700 bg-ink-900/60 px-3 py-1.5">
                    <span className="min-w-0 font-mono text-xs leading-5 break-all text-mist-200">{example.folder}</span>
                    <span className="flex shrink-0 items-center gap-2">
                      {typeof example.title_id === 'number' && (
                        <Link to={`/titel/${example.title_id}`} className="text-xs font-medium text-accent-400 hover:underline">
                          {t('settings.files.companions.openTitle')}
                        </Link>
                      )}
                      {REPLACEABLE.has(row.state) && (
                        <Button size="sm" variant="ghost" onClick={() => onReplace(example)} aria-label={t('settings.files.companions.replaceLabel', { folder: example.folder })}>
                          {t('settings.files.companions.replace')}
                        </Button>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
              {more > 0 && <p className="text-xs text-mist-500">{t('settings.files.companions.report.more', { count: more, value: number(more) })}</p>}
            </div>
          )
        })}
    </section>
  )
}

/** "Ersetzen" asks first: the old file goes into the recycle folder, then nexcrate writes its own. */
function ReplaceDialog({ example, onClose, onReplaced }: { example: CompanionExample; onClose: () => void; onReplaced: (state: string) => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function replace() {
    if (busy) return
    setBusy(true)
    setProblem(null)
    try {
      const result = await companionsApi.replace(example.version_id)
      onReplaced(typeof result?.state === 'string' ? result.state : 'written')
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  function close() {
    if (!busy) onClose()
  }

  return (
    <Dialog
      open
      title={t('settings.files.companions.replaceTitle', { folder: example.folder })}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void replace()} loading={busy}>
            {t('settings.files.companions.replaceConfirm')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm text-mist-200">{t('settings.files.companions.replaceText')}</p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

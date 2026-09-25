import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { renameApi, resultNumber, resultReasons, type RenameJob, type RenameKind, type RenameLastRun, type RenamePreview, type RenameUnit } from '../../api/rename'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, KeyFigure, Section, Spinner } from '../../components/ui'
import { formatDateTime, formatNumber } from '../../lib/format'
import { RenameSteps } from './RenameSteps'
import { skipText, splits, unitSub } from './renameText'
import { useRenameJob } from './useRenameJob'

/**
 * Unterreiter "Umbenennen" unter "Ordner und Benennung" (Antwort G1): je Medienart die Vorschau
 * "vorher, nachher", Auswahl je Titel (Musik je Kuenstler), Bestaetigen, Fortschritt, Ergebnis und der letzte Lauf mit
 * "Rueckgaengig machen". Die Vorschau rechnet der Server im Hintergrund und behaelt sie bis zum naechsten Lauf.
 */
export function RenameSection({ kind }: { kind: RenameKind }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [preview, setPreview] = useState<RenamePreview | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [last, setLast] = useState<RenameLastRun | null>(null)
  const [token, setToken] = useState(0)
  const [search, setSearch] = useState('')
  // Abgewaehlt statt gewaehlt: Ab Werk ist jede fertige Einheit dabei.
  const [off, setOff] = useState<ReadonlySet<number>>(new Set())
  const [open, setOpen] = useState<Readonly<Record<number, RenameUnit | 'loading'>>>({})
  const [confirming, setConfirming] = useState(false)
  const [shownJob, setShownJob] = useState<RenameJob | null>(null)

  const reload = useCallback(() => setToken((count) => count + 1), [])
  const { job, error: jobError, starting, start, running } = useRenameJob(
    useCallback(
      (ended: RenameJob) => {
        if (ended.action !== 'preview') setShownJob(ended)
        reload()
      },
      [reload],
    ),
  )

  useEffect(() => {
    let current = true
    setPreview(null)
    setOpen({})
    setOff(new Set())
    Promise.all([renameApi.preview(kind), renameApi.last()]).then(
      ([found, lastRun]) => {
        if (!current) return
        setPreview(found)
        setLast(lastRun)
        setLoadError(null)
      },
      (problem: unknown) => current && setLoadError(problem),
    )
    return () => {
      current = false
    }
  }, [kind, token])

  const units = preview?.units ?? []
  const ready = units.filter((unit) => unit.skip === null)
  const skipped = units.filter((unit) => unit.skip !== null)
  const needle = search.trim().toLocaleLowerCase(language)
  const shown = useMemo(() => (needle === '' ? ready : ready.filter((unit) => unit.name.toLocaleLowerCase(language).includes(needle))), [ready, needle, language])
  const chosen = ready.filter((unit) => !off.has(unit.id))
  const chosenFiles = chosen.reduce((sum, unit) => sum + unit.files, 0)
  const chosenFolders = chosen.reduce((sum, unit) => sum + unit.folders.length, 0)
  const previewRunning = running && job?.action === 'preview' && job.kind === kind

  const intro = kind === 'music' ? t('settings.rename.intro.music') : kind === 'series' ? t('settings.rename.intro.series') : t('settings.rename.intro.movie')

  async function toggle(unit: RenameUnit) {
    if (open[unit.id] !== undefined) {
      const next = { ...open }
      delete next[unit.id]
      setOpen(next)
      return
    }
    setOpen((current) => ({ ...current, [unit.id]: 'loading' }))
    try {
      const full = await renameApi.unit(kind, unit.id)
      setOpen((current) => (current[unit.id] === undefined ? current : { ...current, [unit.id]: full }))
    } catch {
      setOpen((current) => (current[unit.id] === undefined ? current : { ...current, [unit.id]: unit }))
    }
  }

  function choose(unit: RenameUnit, on: boolean) {
    setOff((current) => {
      const next = new Set(current)
      if (on) next.delete(unit.id)
      else next.add(unit.id)
      return next
    })
  }

  async function runChosen() {
    setConfirming(false)
    const ids = chosen.length === ready.length ? null : chosen.map((unit) => unit.id)
    await start(() => renameApi.run(kind, ids))
  }

  const stateLine = previewRunning
    ? t('settings.rename.computing', { done: formatNumber(job.done, language), total: formatNumber(job.total, language) })
    : preview?.computed_at
      ? t('settings.rename.computedAt', { time: formatDateTime(preview.computed_at, language) })
      : t('settings.rename.notComputed')

  return (
    <div className="flex flex-col gap-4">
      <Section
        title={t('settings.rename.title')}
        intro={intro}
        actions={
          <Button variant="ghost" onClick={() => void start(() => renameApi.computePreview(kind))} loading={starting && !running} disabled={running}>
            <Symbol name="refresh" />
            {preview?.computed_at ? t('settings.rename.recompute') : t('settings.rename.compute')}
          </Button>
        }
      >
        <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
          {previewRunning && <Spinner className="h-3.5 w-3.5" />}
          {stateLine}
        </p>
        {jobError !== null && <FormMessage>{errorText(t, jobError)}</FormMessage>}
        {loadError !== null && <FormMessage>{errorText(t, loadError)}</FormMessage>}

        {preview?.counts && (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <KeyFigure label={kind === 'music' ? t('settings.rename.counts.artists') : t('settings.rename.counts.titles')} value={formatNumber(preview.counts.titles, language)} />
            <KeyFigure label={t('settings.rename.counts.files')} value={formatNumber(preview.counts.files, language)} />
            <KeyFigure label={t('settings.rename.counts.folders')} value={formatNumber(preview.counts.folders, language)} />
            <KeyFigure label={t('settings.rename.counts.skipped')} value={formatNumber(preview.counts.skipped, language)} />
          </div>
        )}

        {preview?.computed_at && ready.length === 0 && skipped.length === 0 && (
          <FormMessage tone="ok" role="status">
            {t('settings.rename.nothing')}
          </FormMessage>
        )}

        {ready.length > 0 && (
          <>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="relative w-full sm:w-80">
                <Symbol name="search" className="pointer-events-none absolute top-1/2 left-3.5 h-4 w-4 -translate-y-1/2 text-mist-600" />
                <input
                  type="search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder={t('settings.rename.search')}
                  aria-label={t('settings.rename.search')}
                  className="w-full rounded-full border border-ink-700 bg-ink-900 py-2 pr-4 pl-10 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
                />
              </div>
              <div className="flex gap-2">
                <Button variant="ghost" size="sm" onClick={() => setOff(new Set())}>
                  {t('settings.rename.all')}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setOff(new Set(ready.map((unit) => unit.id)))}>
                  {t('settings.rename.none')}
                </Button>
              </div>
            </div>
            <ul className="flex flex-col gap-2">
              {shown.map((unit) => {
                const detail = open[unit.id]
                const selected = !off.has(unit.id)
                return (
                  <li key={unit.id} className={'rounded-2xl border border-ink-700 bg-ink-900 ' + (selected ? '' : 'opacity-70')}>
                    <div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 px-4 py-3">
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={(event) => choose(unit, event.target.checked)}
                        aria-label={t('settings.rename.chooseLabel', { name: unit.name })}
                        className="h-4 w-4 accent-accent-500"
                      />
                      <div className="min-w-0">
                        <p className="font-semibold wrap-anywhere text-mist-100">{unit.name}</p>
                        <p className="text-xs text-mist-500">{unitSub(t, unit)}</p>
                      </div>
                      <div className="flex flex-wrap items-center justify-end gap-2">
                        {splits(unit) && <Badge tone="accent">{t('settings.rename.split')}</Badge>}
                        <Button variant="ghost" size="sm" onClick={() => void toggle(unit)} aria-expanded={detail !== undefined} aria-label={t('settings.rename.showLabel', { name: unit.name })}>
                          <Symbol name={detail !== undefined ? 'chevronDown' : 'chevron'} />
                          {t('settings.rename.show')}
                        </Button>
                      </div>
                    </div>
                    {detail !== undefined && (
                      <div className="border-t border-ink-700 px-4 py-3">
                        {detail === 'loading' ? (
                          <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
                            <Spinner />
                            {t('common.loading')}
                          </p>
                        ) : (
                          <RenameSteps unit={detail} />
                        )}
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          </>
        )}

        {skipped.length > 0 && (
          <div className="flex flex-col gap-2 rounded-2xl border border-dashed border-ink-600 px-4 py-3">
            <h3 className="text-sm font-semibold text-mist-300">{t('settings.rename.skippedTitle', { count: skipped.length })}</h3>
            <ul className="flex flex-col gap-1 text-sm text-mist-400">
              {skipped.map((unit) => (
                <li key={unit.id} className="wrap-anywhere">
                  <span className="font-semibold text-mist-300">{unit.name}</span>: {skipText(t, unit.skip ?? '', unit.skip_values)}
                </li>
              ))}
            </ul>
          </div>
        )}

        {ready.length > 0 && (
          <div className="sticky bottom-3 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-ink-700 bg-ink-850 px-4 py-3 shadow-lg">
            <p className="text-sm text-mist-400">{t('settings.rename.chosen', { chosen: formatNumber(chosen.length, language), total: formatNumber(ready.length, language) })}</p>
            <Button onClick={() => setConfirming(true)} disabled={chosen.length === 0 || running}>
              {t('settings.rename.run', { count: chosen.length })}
            </Button>
          </div>
        )}
      </Section>

      {last !== null && (
        <Section title={t('settings.rename.last.title')}>
          <p className="text-sm text-mist-300">
            {last.state === 'undone'
              ? t('settings.rename.last.undone', { time: formatDateTime(last.undone_at ?? last.started_at, language) })
              : t('settings.rename.last.done', {
                  time: formatDateTime(last.finished_at ?? last.started_at, language),
                  files: formatNumber(Number(last.counts.files ?? 0), language),
                  folders: formatNumber(Number(last.counts.folders ?? 0), language),
                })}
          </p>
          {last.can_undo && (
            <div className="flex flex-col items-start gap-1.5">
              <Button variant="ghost" onClick={() => void start(renameApi.undo)} disabled={running}>
                <Symbol name="back" />
                {t('settings.rename.last.undo')}
              </Button>
              <p className="text-xs text-mist-500">{t('settings.rename.last.undoHint')}</p>
            </div>
          )}
        </Section>
      )}

      {confirming && (
        <Dialog
          open
          title={t('settings.rename.confirm.title')}
          onClose={() => setConfirming(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setConfirming(false)}>
                {t('common.actions.cancel')}
              </Button>
              <Button onClick={() => void runChosen()}>{t('settings.rename.confirm.go')}</Button>
            </>
          }
        >
          <div className="flex flex-col gap-3 text-sm text-mist-300">
            <p>
              {t('settings.rename.confirm.text', {
                units: kind === 'music' ? t('settings.rename.confirm.artists', { count: chosen.length }) : t('settings.rename.confirm.titles', { count: chosen.length }),
                files: formatNumber(chosenFiles, language),
                folders: formatNumber(chosenFolders, language),
              })}
            </p>
            <p>{t('settings.rename.confirm.players')}</p>
          </div>
        </Dialog>
      )}

      {(running && job !== null && job.action !== 'preview') || shownJob !== null ? (
        <RunDialog job={running && job !== null && job.action !== 'preview' ? job : shownJob} onClose={() => setShownJob(null)} />
      ) : null}
    </div>
  )
}

function RunDialog({ job, onClose }: { job: RenameJob | null; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  if (job === null) return null
  const language = i18n.language
  const running = job.state === 'running'
  const percent = job.total > 0 ? Math.round((job.done / job.total) * 100) : 0
  const reasons = resultReasons(job)
  return (
    <Dialog
      open
      title={job.action === 'undo' ? t('settings.rename.running.undoTitle') : t('settings.rename.running.title')}
      onClose={running ? () => undefined : onClose}
      footer={running ? undefined : <Button onClick={onClose}>{t('settings.rename.running.close')}</Button>}
    >
      <div className="flex flex-col gap-3">
        <div className="h-2 overflow-hidden rounded-full bg-ink-700" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
          <div className="h-full rounded-full bg-accent-500 transition-[width]" style={{ width: `${running ? percent : 100}%` }} />
        </div>
        <p className="text-sm text-mist-300" role="status">
          {running
            ? t('settings.rename.running.progress', { done: formatNumber(job.done, language), total: formatNumber(job.total, language) })
            : job.state === 'failed'
              ? t('settings.rename.running.failed')
              : job.action === 'undo'
                ? t('settings.rename.running.undone', { undone: resultNumber(job, 'undone'), kept: resultNumber(job, 'kept') })
                : t('settings.rename.running.done', {
                    done: resultNumber(job, 'done'),
                    files: formatNumber(resultNumber(job, 'files'), language),
                    folders: formatNumber(resultNumber(job, 'folders'), language),
                    skipped: resultNumber(job, 'skipped'),
                    failed: resultNumber(job, 'failed'),
                  })}
        </p>
        {!running && reasons.length > 0 && (
          <ul className="flex flex-col gap-1 text-sm text-mist-400">
            {reasons.map(([code, count]) => (
              <li key={code}>{t('settings.rename.running.reason', { count, reason: skipTextFor(t, code) })}</li>
            ))}
          </ul>
        )}
      </div>
    </Dialog>
  )
}

function skipTextFor(t: Parameters<typeof skipText>[0], code: string): string {
  return skipText(t, code)
}

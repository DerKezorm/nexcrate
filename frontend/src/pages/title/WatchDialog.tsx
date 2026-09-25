import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, errorText } from '../../api/client'
import { libraryApi } from '../../api/library'
import type { SeasonBrief, TitleDetail, TitleVersion, WatchChange, WatchRequest, WatchRule } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, FormMessage, SelectField, Spinner } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { SeriesAutomaticNote } from './SeriesAutomaticNote'
import { definitionIdOf, regularSeasons, ruleLabel, WATCH_RULES } from './seriesText'

function requestOf(rule: WatchRule, from: number): WatchRequest {
  return rule === 'from_season' ? { rule, from_season: from } : { rule }
}

/**
 * "Überwachen ändern" fuer eine eigene Fassung einer Serie. Jede Aenderung der Auswahl fragt den Server, was die Regel
 * aendern wuerde; eine alte Frage bricht dabei ab. Gespeichert wird erst auf Knopfdruck.
 */
export function WatchDialog({
  title,
  version,
  seasons,
  onClose,
  onChanged,
  onStale,
}: {
  title: TitleDetail
  version: TitleVersion
  seasons: readonly SeasonBrief[]
  onClose: () => void
  onChanged: (detail: TitleDetail) => void
  /** Der Server kennt einen neueren Stand, etwa weil Sonarr die Fassung inzwischen fuellt. */
  onStale: () => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const versionId = definitionIdOf(version)
  const numbers = regularSeasons(seasons)
    .map((season) => season.number)
    .sort((a, b) => a - b)
  const [rule, setRule] = useState<WatchRule>(version.watch?.rule ?? 'all')
  const [from, setFrom] = useState<number>(version.watch?.from_season ?? numbers[0] ?? 1)
  const [preview, setPreview] = useState<WatchChange | null>(null)
  const [previewProblem, setPreviewProblem] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  useEffect(() => {
    if (versionId === null) return
    const abort = new AbortController()
    setPreview(null)
    setPreviewProblem(null)
    libraryApi.watchPreview(title.id, versionId, requestOf(rule, from), abort.signal).then(
      (result) => {
        if (!abort.signal.aborted) setPreview(result)
      },
      (error: unknown) => {
        if (!abort.signal.aborted) setPreviewProblem(error)
      },
    )
    return () => abort.abort()
  }, [title.id, versionId, rule, from])

  async function save() {
    if (busy || versionId === null) return
    setBusy(true)
    setProblem(null)
    try {
      onChanged(await libraryApi.changeWatch(title.id, versionId, requestOf(rule, from)))
    } catch (error) {
      setProblem(error)
      setBusy(false)
      if (error instanceof ApiError && error.code === 'version_fed_by_source') onStale()
    }
  }

  function close() {
    if (!busy) onClose()
  }

  const number = (value: number) => formatNumber(value, language)

  return (
    <Dialog
      open
      title={t('series.watchDialog.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void save()} loading={busy} disabled={versionId === null}>
            {t('common.actions.save')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-mist-400">{t('series.watchDialog.sub', { title: title.title, label: version.label })}</p>
        <SelectField label={t('series.watchDialog.rule')} value={rule} disabled={busy} onChange={(event) => setRule(event.target.value as WatchRule)}>
          {WATCH_RULES.filter((option) => option !== 'from_season' || numbers.length > 0).map((option) => (
            <option key={option} value={option}>
              {ruleLabel(t, option)}
            </option>
          ))}
        </SelectField>
        {rule === 'from_season' && (
          <SelectField label={t('series.watchDialog.fromSeason')} value={String(from)} disabled={busy} onChange={(event) => setFrom(Number(event.target.value))}>
            {numbers.map((value) => (
              <option key={value} value={value}>
                {t('series.seasons.season', { number: value })}
              </option>
            ))}
          </SelectField>
        )}
        <p className="text-sm text-mist-500">{t('series.watchDialog.specials')}</p>
        {previewProblem !== null ? (
          <FormMessage>{errorText(t, previewProblem)}</FormMessage>
        ) : (
          <div role="status" className="flex flex-col gap-1 rounded-xl border border-info-500/30 bg-info-500/5 px-3.5 py-2.5 text-sm tabular-nums">
            {preview === null ? (
              <p className="flex items-center gap-2 text-mist-400">
                <Spinner />
                {t('series.watchDialog.counting')}
              </p>
            ) : (
              <>
                <p className="font-medium text-mist-100">{t('series.watchDialog.change', { added: number(preview.added), removed: number(preview.removed) })}</p>
                {preview.overridden > 0 && (
                  <p className="text-bad-500">{t('series.watchDialog.overridden', { count: preview.overridden, value: number(preview.overridden) })}</p>
                )}
                <p className="text-mist-400">{t('series.watchDialog.after', { watched: number(preview.watched), aired: number(preview.aired) })}</p>
              </>
            )}
          </div>
        )}
        <SeriesAutomaticNote />
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

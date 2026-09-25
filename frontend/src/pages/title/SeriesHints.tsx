import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { libraryApi } from '../../api/library'
import type { SeriesBlock, SeriesType, TitleDetail } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button, SelectField } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatNumber } from '../../lib/format'
import { numberingEpisodesText } from './seriesText'

/** Der Satz unter der Wahl, je Art. Woertliche Schluessel, damit der Waechter sie sieht. */
const HINTS = {
  standard: 'series.type.standardHint',
  daily: 'series.type.dailyHint',
  anime: 'series.type.animeHint',
} as const

/**
 * Die Hinweise ueber Fassungen und Staffeln einer Serie: wie Sonarr anders zaehlt, welche Folgen TMDB nachtraeglich
 * eingetragen hat, und die Wahl der Art (B1: normal, taeglich, Anime; vorher ein Schalter, der nur taeglich kannte).
 */
export function SeriesHints({
  title,
  series,
  onChanged,
  onNumbering,
}: {
  title: TitleDetail
  series: SeriesBlock
  onChanged: (detail: TitleDetail) => void
  /** Seit S3: oeffnet den Dialog "Nummerierung". */
  onNumbering?: () => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const notify = useNotice()
  const [lateBusy, setLateBusy] = useState(false)
  const [typeBusy, setTypeBusy] = useState(false)
  const numbering = series.numbering
  const differences = numbering?.differences ?? []
  // So viele abweichende Staffeln stehen einzeln da, der Rest als Zahl
  const SHOWN_DIFFERENCES = 6
  const late = typeof series.late_episodes === 'number' ? series.late_episodes : 0
  // Fuellt eine Sonarr-Verbindung die Serie, bestimmt sie die Art; der Server weist eine Aenderung hier mit 409 ab.
  const fed = Boolean(series.type_fed)
  const chosen = (series.type === 'daily' || series.type === 'anime' ? series.type : 'standard') as SeriesType

  async function watchLate() {
    setLateBusy(true)
    try {
      onChanged(await libraryApi.watchLate(title.id))
      notify(t('series.late.done'))
    } catch (error) {
      notify(errorText(t, error))
    } finally {
      setLateBusy(false)
    }
  }

  async function changeType(seriesType: SeriesType) {
    setTypeBusy(true)
    try {
      onChanged(await libraryApi.changeSeriesType(title.id, seriesType))
      notify(t('series.type.saved'))
    } catch (error) {
      notify(errorText(t, error))
    } finally {
      setTypeBusy(false)
    }
  }

  return (
    <div className="flex max-w-3xl flex-col gap-2">
      {numbering && numbering.source && numbering.tmdb && (
        <div role="note" className="flex items-start gap-3 rounded-xl border border-info-500/30 bg-info-500/5 px-4 py-3 text-sm">
          <Symbol name="swap" className="mt-0.5 h-4 w-4 shrink-0 text-info-500" />
          <div className="flex min-w-0 flex-col gap-1">
            <p className="font-semibold text-mist-100">{t('series.numbering.title', { name: numbering.source.name })}</p>
            <p className="text-mist-400">{t('series.numbering.why', { name: numbering.source.name })}</p>
            {differences.length > 0 && (
              <ul className="flex flex-col text-mist-300 tabular-nums wrap-anywhere">
                {differences.slice(0, SHOWN_DIFFERENCES).map((difference) => (
                  <li key={difference.season}>
                    {t('series.numbering.season', {
                      number: formatNumber(difference.season, language),
                      tmdb: numberingEpisodesText(t, difference.tmdb, language),
                      source: numberingEpisodesText(t, difference.source, language),
                      name: numbering.source.name,
                    })}
                  </li>
                ))}
                {differences.length > SHOWN_DIFFERENCES && (
                  <li>
                    {t('series.numbering.more', {
                      count: differences.length - SHOWN_DIFFERENCES,
                      value: formatNumber(differences.length - SHOWN_DIFFERENCES, language),
                    })}
                  </li>
                )}
              </ul>
            )}
            {numbering.unmatched > 0 && (
              <p className="text-mist-400">
                {t('series.numbering.unmatched', { count: numbering.unmatched, value: formatNumber(numbering.unmatched, language), name: numbering.source.name })}
                {(numbering.unmatched_specials ?? 0) > 0 &&
                  ' ' + t('series.numbering.unmatchedSpecials', { count: numbering.unmatched_specials ?? 0, value: formatNumber(numbering.unmatched_specials ?? 0, language) })}
              </p>
            )}
            {onNumbering && (
              <div className="pt-1">
                <Button size="sm" variant="ghost" onClick={onNumbering}>
                  <Symbol name="swap" />
                  {t('series.numberingDialog.open')}
                </Button>
              </div>
            )}
          </div>
        </div>
      )}

      {late > 0 && (
        <div role="note" className="flex flex-wrap items-start gap-3 rounded-xl border border-bad-500/40 bg-bad-500/10 px-4 py-3 text-sm">
          <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0 text-bad-500" />
          <p className="min-w-0 flex-1 text-mist-200">{t('series.late.text', { count: late, value: formatNumber(late, language) })}</p>
          <Button size="sm" variant="ghost" loading={lateBusy} onClick={() => void watchLate()}>
            {!lateBusy && <Symbol name="eye" />}
            {t('series.late.action')}
          </Button>
        </div>
      )}

      <div className="flex flex-col gap-2 rounded-xl border border-ink-700 bg-ink-900/60 px-4 py-3">
        <SelectField
          label={t('series.type.label')}
          hint={fed ? t('series.type.fed') : t(HINTS[chosen])}
          value={chosen}
          disabled={fed || typeBusy}
          onChange={(event) => void changeType(event.target.value as SeriesType)}
        >
          <option value="standard">{t('series.type.standard')}</option>
          <option value="daily">{t('series.type.daily')}</option>
          <option value="anime">{t('series.type.anime')}</option>
        </SelectField>
      </div>
    </div>
  )
}

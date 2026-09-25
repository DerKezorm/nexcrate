import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { libraryApi } from '../../api/library'
import type { SeriesType } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, FormMessage, SelectField } from '../../components/ui'
import { formatNumber } from '../../lib/format'

/** Der Umfang der Aenderung: entweder die markierten Serien oder die ganze Ansicht. */
export type SeriesTypeScope = {
  ids: number[] | null
  state: string | null
  q: string | null
  tag: string | null
}

/** Der Satz unter der Wahl, je Art. Woertliche Schluessel, damit der Waechter sie sieht. */
const HINTS = {
  standard: 'series.type.standardHint',
  daily: 'series.type.dailyHint',
  anime: 'series.type.animeHint',
} as const

/**
 * "Art der Serie" fuer die Auswahl (B1). Wie Sonarrs Massenbearbeitung, nur mit dem Unterschied,
 * dass Serien, die eine Sonarr-Verbindung fuellt, aussen vor bleiben: dort entscheidet Sonarr, und der naechste Lauf
 * wuerde die Aenderung wieder zurueckholen. Wie viele das waren, sagt die Meldung danach.
 */
export function SeriesTypeDialog({
  scope,
  count,
  onClose,
  onDone,
}: {
  scope: SeriesTypeScope
  count: number
  onClose: () => void
  onDone: (result: { changed: number; fed: number }) => void
}) {
  const { t, i18n } = useTranslation()
  const [chosen, setChosen] = useState<SeriesType>('anime')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function apply() {
    setBusy(true)
    setProblem(null)
    try {
      onDone(
        await libraryApi.setSeriesTypeAll({
          series_type: chosen,
          title_ids: scope.ids,
          state: scope.state,
          q: scope.q,
          tag: scope.tag,
        }),
      )
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  return (
    <Dialog
      open
      title={t('library.select.seriesType.title', { count, value: formatNumber(count, i18n.language) })}
      onClose={() => !busy && onClose()}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void apply()} loading={busy}>
            {t('library.select.seriesType.apply')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <SelectField
          label={t('series.type.label')}
          hint={t(HINTS[chosen])}
          value={chosen}
          disabled={busy}
          onChange={(event) => setChosen(event.target.value as SeriesType)}
        >
          <option value="standard">{t('series.type.standard')}</option>
          <option value="daily">{t('series.type.daily')}</option>
          <option value="anime">{t('series.type.anime')}</option>
        </SelectField>
        <p className="text-xs text-mist-500">{t('library.select.seriesType.hint')}</p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

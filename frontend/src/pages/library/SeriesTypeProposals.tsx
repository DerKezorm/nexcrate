import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { libraryApi } from '../../api/library'
import type { SeriesType, SeriesTypeProposals as Proposals } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, FormMessage, Spinner } from '../../components/ui'
import { formatNumber } from '../../lib/format'

/** Die Namen der Arten, woertlich, damit der Waechter sie sieht. */
const TYPE_KEYS = {
  standard: 'library.typeProposals.types.standard',
  daily: 'library.typeProposals.types.daily',
  anime: 'library.typeProposals.types.anime',
} as const

function typeKey(value: string) {
  return value in TYPE_KEYS ? TYPE_KEYS[value as keyof typeof TYPE_KEYS] : TYPE_KEYS.standard
}

/**
 * "Art pruefen" (B5): was TMDB fuer jede Serie vorschlaegt, gegen die Art, die sie hat. Vor A1
 * angelegte Serien hatten nie einen Vorschlag; der Knopf fragt TMDB einmal je Serie. Nichts aendert sich von selbst:
 * uebernommen wird, was angehakt ist, ueber dieselbe Massenaenderung wie in der Auswahl-Leiste. Serien, die eine
 * Sonarr-Verbindung fuellt, fehlen in der Liste; dort entscheidet Sonarr.
 */
export function SeriesTypeProposals({ onClose, onDone }: { onClose: () => void; onDone: (message: string) => void }) {
  const { t, i18n } = useTranslation()
  const number = (value: number) => formatNumber(value, i18n.language)
  const [data, setData] = useState<Proposals | null>(null)
  const [chosen, setChosen] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  const load = useCallback(async () => {
    try {
      const read = await libraryApi.seriesTypeProposals()
      setData(read)
      // Jeder Vorschlag ist angehakt; wer einen nicht will, nimmt den Haken weg.
      setChosen(new Set(read.proposals.map((item) => item.title_id)))
    } catch (error) {
      setProblem(error)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // Solange TMDB gefragt wird, liest der Dialog alle zwei Sekunden nach.
  const running = data?.run.state === 'running'
  useEffect(() => {
    if (!running) return
    const timer = window.setTimeout(() => void load(), 2000)
    return () => window.clearTimeout(timer)
  }, [running, data, load])

  async function ask(everything: boolean) {
    setProblem(null)
    try {
      await libraryApi.runSeriesTypeProposals(everything)
      await load()
    } catch (error) {
      setProblem(error)
    }
  }

  async function take() {
    if (data === null || chosen.size === 0) return
    setBusy(true)
    setProblem(null)
    try {
      const byType = new Map<string, number[]>()
      for (const item of data.proposals) {
        if (chosen.has(item.title_id)) byType.set(item.proposed, [...(byType.get(item.proposed) ?? []), item.title_id])
      }
      let changed = 0
      for (const [seriesType, ids] of byType) {
        changed += (await libraryApi.setSeriesTypeAll({ series_type: seriesType as SeriesType, title_ids: ids })).changed
      }
      onDone(t('library.typeProposals.done', { count: changed, value: number(changed) }))
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  function toggle(id: number) {
    setChosen((before) => {
      const next = new Set(before)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const proposals = data?.proposals ?? []
  return (
    <Dialog
      open
      wide
      title={t('library.typeProposals.title')}
      onClose={() => !busy && onClose()}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void take()} loading={busy} disabled={chosen.size === 0 || running}>
            {t('library.typeProposals.take', { count: chosen.size, value: number(chosen.size) })}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-mist-400">{t('library.typeProposals.hint')}</p>
        {data === null && problem === null && (
          <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
            <Spinner /> {t('library.typeProposals.loading')}
          </p>
        )}
        {data !== null && (
          <div className="flex flex-wrap items-center gap-3" role="status">
            {running ? (
              <span className="flex items-center gap-2 text-sm text-mist-300">
                <Spinner /> {t('library.typeProposals.running', { done: number(data.run.done), total: number(data.run.total) })}
              </span>
            ) : data.unknown > 0 ? (
              <>
                <span className="text-sm text-mist-300">{t('library.typeProposals.unknown', { count: data.unknown, value: number(data.unknown) })}</span>
                <Button size="sm" variant="ghost" onClick={() => void ask(false)}>
                  {t('library.typeProposals.ask')}
                </Button>
              </>
            ) : (
              <>
                <span className="text-sm text-mist-500">{t('library.typeProposals.allKnown')}</span>
                <Button size="sm" variant="ghost" onClick={() => void ask(true)}>
                  {t('library.typeProposals.askAll')}
                </Button>
              </>
            )}
          </div>
        )}
        {data?.run.problem && <FormMessage>{t('library.typeProposals.problem')}</FormMessage>}
        {data !== null && !running && proposals.length === 0 && <p className="text-sm text-mist-300">{t('library.typeProposals.none')}</p>}
        {proposals.length > 0 && (
          <ul className="flex flex-col gap-1.5" aria-label={t('library.typeProposals.title')}>
            {proposals.map((item) => (
              <li key={item.title_id}>
                <label className="flex min-w-0 cursor-pointer items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/60 px-3 py-2">
                  <input type="checkbox" checked={chosen.has(item.title_id)} onChange={() => toggle(item.title_id)} className="accent-accent-500" />
                  <span className="min-w-0 flex-1 text-sm text-mist-100 wrap-anywhere">
                    {item.title}
                    {item.year !== null && <span className="text-mist-500"> ({item.year})</span>}
                  </span>
                  <span className="shrink-0 text-xs text-mist-400">
                    {t('library.typeProposals.change', { from: t(typeKey(item.series_type)), to: t(typeKey(item.proposed)) })}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

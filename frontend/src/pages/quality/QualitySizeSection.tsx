import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { expertApi } from '../../api/expert'
import type { MediaKind, QualitySizeRow } from '../../api/types'
import { Button, FormMessage, Section, Spinner } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatGbPerHour } from '../profiles/profileText'

/** Radarr rechnet in MB je Minute; nexcrate zeigt daneben, was das je Stunde ergibt. */
function gbPerHour(mbPerMin: number, language: string): string {
  return formatGbPerHour((mbPerMin * 60) / 1024, language)
}

function asNumber(value: string): number | null {
  const text = value.trim().replace(',', '.')
  if (text === '') return null
  const parsed = Number(text)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null
}

type Draft = { min: string; preferred: string; max: string }

function draftOf(items: QualitySizeRow[]): Record<string, Draft> {
  const draft: Record<string, Draft> = {}
  for (const item of items) {
    draft[item.quality] = {
      min: String(item.min_mb_per_min),
      preferred: item.preferred_mb_per_min === null ? '' : String(item.preferred_mb_per_min),
      max: item.max_mb_per_min === null ? '' : String(item.max_mb_per_min),
    }
  }
  return draft
}

/**
 * Was eine Qualitaet wiegen darf, wie Radarrs Seite "Qualitaet": je Qualitaet ein Mindestes und ein Hoechstes
 * in MB je Minute. Die Groessen gelten fuer alle Profile dieser Art, nicht je Profil, genau wie dort.
 *
 * ⚠️ Ein leeres Hoechstes heisst ohne Obergrenze. Ein Hoechstes unter dem Mindesten weist der Server ab.
 *
 * Dazwischen steht Radarrs dritter Wert, der Wunschwert: er sperrt nichts aus, er entscheidet nur zwischen Releases,
 * die in allem anderen gleich sind. Leer heisst, das groessere gewinnt. Ausserhalb der Grenzen weist der Server ihn ab.
 */
export function QualitySizeSection({ kind }: { kind: MediaKind }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const notify = useNotice()
  const [items, setItems] = useState<QualitySizeRow[] | null>(null)
  const [draft, setDraft] = useState<Record<string, Draft>>({})
  const [loadError, setLoadError] = useState<unknown>(null)
  const [problem, setProblem] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let dropped = false
    setItems(null)
    setLoadError(null)
    expertApi
      .quality(kind)
      .then((list) => {
        if (dropped) return
        setItems(list.items)
        setDraft(draftOf(list.items))
      })
      .catch((error: unknown) => {
        if (!dropped) setLoadError(error)
      })
    return () => {
      dropped = true
    }
  }, [kind])

  const none = t('quality.sizes.noLimit')
  const changed =
    items !== null &&
    items.some((item) => {
      const entry = draft[item.quality]
      return (
        entry !== undefined &&
        (asNumber(entry.min) !== item.min_mb_per_min || asNumber(entry.max) !== item.max_mb_per_min || asNumber(entry.preferred) !== item.preferred_mb_per_min)
      )
    })

  function set(quality: string, part: keyof Draft, value: string) {
    setDraft((before) => ({ ...before, [quality]: { ...before[quality], [part]: value } }))
  }

  async function save() {
    if (busy || items === null) return
    setBusy(true)
    setProblem(null)
    const body = items.map((item) => {
      const entry = draft[item.quality] ?? { min: String(item.min_mb_per_min), preferred: '', max: '' }
      return { quality: item.quality, min_mb_per_min: asNumber(entry.min) ?? 0, max_mb_per_min: asNumber(entry.max), preferred_mb_per_min: asNumber(entry.preferred) }
    })
    try {
      const saved = await expertApi.saveQuality(kind, body)
      setItems(saved.items)
      setDraft(draftOf(saved.items))
      notify(t('quality.sizes.saved'))
    } catch (error: unknown) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section
      title={t('quality.sizes.title')}
      intro={t('quality.sizes.intro')}
      actions={
        <Button onClick={() => void save()} disabled={!changed || busy} loading={busy}>
          {t('quality.sizes.save')}
        </Button>
      }
    >
      {items === null ? (
        loadError !== null ? (
          <FormMessage>{errorText(t, loadError)}</FormMessage>
        ) : (
          <p className="flex items-center gap-2 text-sm text-mist-500">
            <Spinner /> {t('quality.sizes.loading')}
          </p>
        )
      ) : (
        <div className="flex flex-col gap-3">
          {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
          <div className="overflow-x-auto rounded-xl border border-ink-700">
            <table className="w-full text-sm" aria-label={t('quality.sizes.table')}>
              <thead className="bg-ink-900 text-left text-xs text-mist-500">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t('quality.sizes.quality')}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t('quality.sizes.min')}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t('quality.sizes.preferred')}
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t('quality.sizes.max')}
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    {t('quality.sizes.perHour')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => {
                  const entry = draft[item.quality] ?? { min: '', preferred: '', max: '' }
                  return (
                    <tr key={item.quality} className="border-t border-ink-700/60">
                      <td className="px-3 py-2 font-mono text-xs wrap-anywhere text-mist-200">{item.quality}</td>
                      <td className="px-3 py-2">
                        <input
                          type="number"
                          min={0}
                          step="0.1"
                          value={entry.min}
                          onChange={(event) => set(item.quality, 'min', event.target.value)}
                          aria-label={t('quality.sizes.minOf', { quality: item.quality })}
                          className="w-24 rounded-lg border border-ink-700 bg-ink-900 px-2 py-1 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
                        />
                      </td>
                      <td className="px-3 py-2">
                        <input
                          type="number"
                          min={0}
                          step="0.1"
                          value={entry.preferred}
                          placeholder={t('quality.sizes.noPreferred')}
                          onChange={(event) => set(item.quality, 'preferred', event.target.value)}
                          aria-label={t('quality.sizes.preferredOf', { quality: item.quality })}
                          className="w-32 rounded-lg border border-ink-700 bg-ink-900 px-2 py-1 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
                        />
                      </td>
                      <td className="px-3 py-2">
                        <input
                          type="number"
                          min={0}
                          step="0.1"
                          value={entry.max}
                          placeholder={none}
                          onChange={(event) => set(item.quality, 'max', event.target.value)}
                          aria-label={t('quality.sizes.maxOf', { quality: item.quality })}
                          className="w-32 rounded-lg border border-ink-700 bg-ink-900 px-2 py-1 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
                        />
                      </td>
                      <td className="px-3 py-2 text-right text-xs text-mist-500 tabular-nums">
                        {asNumber(entry.max) === null
                          ? t('quality.sizes.fromGb', { min: gbPerHour(asNumber(entry.min) ?? 0, language) })
                          : t('quality.sizes.rangeGb', { min: gbPerHour(asNumber(entry.min) ?? 0, language), max: gbPerHour(asNumber(entry.max) ?? 0, language) })}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Section>
  )
}

import { useEffect, useRef, useState } from 'react'

import { libraryApi } from '../../api/library'
import type { SeriesAdd, SeriesPreview, SeriesVersionChoice, WatchRule } from '../../api/types'

/** Gezaehlt wird erst, wenn so lange nichts mehr geaendert wurde. */
export const SERIES_PREVIEW_DELAY_MS = 250

/** Was je Serienfassung im Dialog gewaehlt ist. `from` null heisst: noch keine Staffel festgelegt. */
export type SeriesPick = { on: boolean; rule: WatchRule; from: number | null }

export const DEFAULT_PICK: SeriesPick = { on: false, rule: 'all', from: null }

/** Die Wahl einer Fassung, wie der Server sie annimmt. Solange keine Staffel feststeht, beginnt "Ab Staffel" bei 1. */
export function choiceOf(versionId: number, pick: SeriesPick | undefined): SeriesVersionChoice {
  const current = pick ?? DEFAULT_PICK
  return { version_id: versionId, rule: current.rule, from_season: current.rule === 'from_season' ? (current.from ?? 1) : null }
}

/**
 * Zaehlt beim Server, was das Hinzufuegen ueberwachen wuerde. Nach einer kurzen Pause, und nur die
 * neueste Anfrage zaehlt: Eine langsame alte Antwort ueberschreibt keine neuere. Gespeichert wird nichts.
 */
export function useSeriesPreview(body: SeriesAdd | null) {
  const key = body === null ? '' : JSON.stringify(body)
  const bodyRef = useRef(body)
  bodyRef.current = body
  const [answer, setAnswer] = useState<{ tmdbId: number; preview: SeriesPreview } | null>(null)
  const [counting, setCounting] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    const current = bodyRef.current
    if (current === null) {
      setCounting(false)
      setError(null)
      return
    }
    const abort = new AbortController()
    setCounting(true)
    const timer = window.setTimeout(() => {
      libraryApi.previewSeries(current, abort.signal).then(
        (preview) => {
          if (abort.signal.aborted) return
          setAnswer({ tmdbId: current.tmdb_id, preview })
          setError(null)
          setCounting(false)
        },
        (problem: unknown) => {
          if (abort.signal.aborted) return
          setError(problem)
          setCounting(false)
        },
      )
    }, SERIES_PREVIEW_DELAY_MS)
    return () => {
      window.clearTimeout(timer)
      abort.abort()
    }
  }, [key])

  // Die Zahlen einer anderen Serie gelten hier nicht, auch nicht fuer einen Augenblick.
  const preview = body !== null && answer !== null && answer.tmdbId === body.tmdb_id ? answer.preview : null
  return { preview, counting: body !== null && counting, error: body === null ? null : error }
}

import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../../api/client'
import { TAKEOVER_POLL_MS, takeoverApi } from '../../api/takeover'
import type { TakeoverCheckRequest, TakeoverJob, TakeoverRequest } from '../../api/types'

export type TakeoverStart = { kind: 'check'; body?: TakeoverCheckRequest } | { kind: 'takeover'; body: TakeoverRequest }

/**
 * Der Auftrag einer Uebernahme, solange das Fenster offen ist.
 *
 * Beim Oeffnen fragt es den neuesten Auftrag nach. Laeuft einer, etwa nach Schliessen und Wiederoeffnen, oder ist eine
 * Uebernahme gerade fertig, zeigt das Fenster ihn. Eine aeltere Pruefung nicht: Sie zeigt einen alten Stand.
 *
 * Ein laufender Auftrag wird jede Sekunde nachgefragt, erst wenn die vorige Antwort da ist. Geht das Fenster zu, hoert
 * das auf. Kennt der Server den Auftrag nicht mehr (404, etwa nach einem Neustart), ist er verloren; bei anderen
 * Fehlern fragt es weiter.
 */
export function useTakeoverJob(sourceId: number) {
  const [job, setJob] = useState<TakeoverJob | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [lost, setLost] = useState(false)
  const [retry, setRetry] = useState(0)
  const mounted = useRef(false)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  useEffect(() => {
    let current = true
    takeoverApi.job(sourceId).then(
      (found) => {
        if (!current) return
        if (found !== null && (found.state === 'running' || (found.kind === 'takeover' && found.state === 'done'))) setJob(found)
        setLoaded(true)
      },
      () => {
        // Ein Fehler zeigt sich spaetestens beim Pruefen.
        if (current) setLoaded(true)
      },
    )
    return () => {
      current = false
    }
  }, [sourceId])

  useEffect(() => {
    if (job === null || job.state !== 'running') return
    let current = true
    const timer = window.setTimeout(() => {
      takeoverApi.job(sourceId).then(
        (next) => {
          if (!current) return
          setError(null)
          if (next === null) {
            // Der Auftrag ist weg (Neustart des Servers).
            setJob(null)
            setLost(true)
            return
          }
          // Ein neues Objekt, auch wenn es noch laeuft: Das plant die naechste Nachfrage.
          setJob(next)
        },
        (problem: unknown) => {
          if (!current) return
          if (problem instanceof ApiError && problem.status === 404) {
            setJob(null)
            setLost(true)
            return
          }
          setError(problem)
          setRetry((count) => count + 1)
        },
      )
    }, TAKEOVER_POLL_MS)
    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [job, retry, sourceId])

  /** true, wenn der Server diesen Auftrag angenommen hat. Laeuft schon einer, folgt das Fenster diesem und sagt false. */
  const start = useCallback(
    async (request: TakeoverStart): Promise<boolean> => {
      setStarting(true)
      setError(null)
      setLost(false)
      try {
        const started = request.kind === 'check' ? await takeoverApi.check(sourceId, request.body) : await takeoverApi.start(sourceId, request.body)
        if (mounted.current) setJob(started)
        return true
      } catch (problem) {
        if (!mounted.current) return false
        if (problem instanceof ApiError && problem.code === 'takeover_running') {
          try {
            const other = await takeoverApi.job(sourceId)
            if (other !== null) {
              if (mounted.current) setJob(other)
              return false
            }
          } catch {
            // Dann steht der erste Fehler da.
          }
        }
        if (mounted.current) setError(problem)
        return false
      } finally {
        if (mounted.current) setStarting(false)
      }
    },
    [sourceId],
  )

  return { job, loaded, starting, error, lost, start }
}

import { useCallback, useEffect, useRef, useState } from 'react'

import { RENAME_POLL_MS, renameApi, type RenameJob } from '../../api/rename'

/**
 * Der Auftrag der Umbenennung (Vorschau, Lauf, Rueckgaengig). Es gibt nur einen zur Zeit. Beim Oeffnen fragt die Seite
 * den neuesten nach, damit ein laufender nach Neuladen weiter zu sehen ist. Ein laufender Auftrag wird jede Sekunde
 * nachgefragt, erst wenn die vorige Antwort da ist. `onFinished` kommt einmal, wenn ein Auftrag endet, den diese Seite
 * laufen gesehen hat.
 */
export function useRenameJob(onFinished: (job: RenameJob) => void) {
  const [job, setJob] = useState<RenameJob | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  const finished = useRef(onFinished)
  finished.current = onFinished
  // Welcher Auftrag laufend gesehen wurde: Nur dessen Ende meldet die Seite.
  const watched = useRef<string | null>(null)

  useEffect(() => {
    let current = true
    renameApi.job().then(
      (found) => {
        if (!current || found === null) return
        if (found.state === 'running') watched.current = found.id
        setJob(found)
      },
      () => undefined,
    )
    return () => {
      current = false
    }
  }, [])

  useEffect(() => {
    if (job === null) return
    if (job.state !== 'running') {
      if (watched.current === job.id) {
        watched.current = null
        finished.current(job)
      }
      return
    }
    let current = true
    const timer = window.setTimeout(() => {
      renameApi.job().then(
        (next) => {
          if (!current) return
          setError(null)
          setJob(next ?? { ...job, state: 'failed', error: 'lost' })
        },
        (problem: unknown) => {
          if (!current) return
          setError(problem)
          // Ein neues Objekt plant die naechste Nachfrage.
          setJob({ ...job })
        },
      )
    }, RENAME_POLL_MS)
    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [job])

  const start = useCallback(async (request: () => Promise<RenameJob>) => {
    setStarting(true)
    setError(null)
    try {
      const started = await request()
      watched.current = started.id
      setJob(started)
      return started
    } catch (problem) {
      setError(problem)
      return null
    } finally {
      setStarting(false)
    }
  }, [])

  return { job, error, starting, start, running: job?.state === 'running' }
}

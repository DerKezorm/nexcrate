import { useCallback, useEffect, useRef, useState } from 'react'

import { IMPORT_POLL_MS, importsApi } from '../../api/imports'
import { sourcesApi } from '../../api/sources'
import type { ImportRun } from '../../api/types'

/**
 * Startet die Uebernahme einer Quelle und fragt alle 2 Sekunden nach, bis sie
 * fertig oder gescheitert ist. Ein Lauf, der beim Oeffnen der Seite schon lief,
 * wird ebenso verfolgt. Geht die Seite zu, hoert das Nachfragen auf.
 *
 * Nachgefragt wird erst, wenn die vorige Antwort da ist. So stapeln sich bei einem
 * langsamen Server keine Anfragen.
 */
export function useImportRun(sourceId: number, initial: ImportRun | null, onFinished: (run: ImportRun) => void) {
  const [run, setRun] = useState<ImportRun | null>(initial)
  const [error, setError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  const mounted = useRef(false)
  const finished = useRef(onFinished)
  finished.current = onFinished

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  useEffect(() => {
    if (run === null || run.status !== 'running') return
    const id = run.id
    let current = true
    const timer = window.setTimeout(() => {
      importsApi.get(id).then(
        (next) => {
          if (!current) return
          // Ein neues Objekt, auch wenn es noch laeuft: Das plant die naechste Nachfrage.
          setRun(next)
          if (next.status !== 'running') finished.current(next)
        },
        (problem: unknown) => {
          if (current) setError(problem)
        },
      )
    }, IMPORT_POLL_MS)
    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [run])

  const start = useCallback(async () => {
    setStarting(true)
    setError(null)
    try {
      const started = await sourcesApi.startImport(sourceId)
      if (!mounted.current) return
      setRun(started)
      if (started.status !== 'running') finished.current(started)
    } catch (problem) {
      if (mounted.current) setError(problem)
    } finally {
      if (mounted.current) setStarting(false)
    }
  }, [sourceId])

  return { run, error, starting, start }
}

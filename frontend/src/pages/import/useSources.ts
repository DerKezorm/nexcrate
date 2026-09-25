import { useCallback, useEffect, useState } from 'react'

import { sourcesApi } from '../../api/sources'
import type { Source } from '../../api/types'

/** Die eingetragenen Quellen, mit `reload` nach jeder Aenderung. */
export function useSources() {
  const [sources, setSources] = useState<Source[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [token, setToken] = useState(0)

  useEffect(() => {
    let current = true
    sourcesApi.list().then(
      (result) => {
        if (!current) return
        setSources(result)
        setError(null)
      },
      (problem: unknown) => {
        if (current) setError(problem)
      },
    )
    return () => {
      current = false
    }
  }, [token])

  const reload = useCallback(() => setToken((count) => count + 1), [])
  return { sources, error, reload }
}

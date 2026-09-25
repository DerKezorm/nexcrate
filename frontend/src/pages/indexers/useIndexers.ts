import { useCallback, useEffect, useState } from 'react'

import { indexersApi } from '../../api/indexers'
import type { Indexer } from '../../api/types'

/** Die eingetragenen Indexer, mit `reload` nach jeder Aenderung. */
export function useIndexers() {
  const [indexers, setIndexers] = useState<Indexer[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [token, setToken] = useState(0)

  useEffect(() => {
    let current = true
    indexersApi.list().then(
      (result) => {
        if (!current) return
        setIndexers(result)
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
  return { indexers, error, reload }
}

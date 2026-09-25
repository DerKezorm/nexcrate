import { useCallback, useEffect, useState } from 'react'

import { apiKeysApi } from '../../api/apiKeys'
import type { ApiKeyList } from '../../api/types'

/** Die Schluessel fuer andere Programme, mit `reload` nach jeder Aenderung. */
export function useApiKeys() {
  const [keys, setKeys] = useState<ApiKeyList | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [token, setToken] = useState(0)

  useEffect(() => {
    let current = true
    apiKeysApi.list().then(
      (result) => {
        if (!current) return
        setKeys(result)
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
  return { keys, error, reload }
}

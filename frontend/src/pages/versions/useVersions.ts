import { useCallback, useEffect, useState } from 'react'

import type { MediaKind, Version } from '../../api/types'
import { versionsApi } from '../../api/versions'

/** Die Fassungen einer Medienart, mit `reload` nach jeder Aenderung. */
export function useVersions(kind: MediaKind) {
  const [versions, setVersions] = useState<Version[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [token, setToken] = useState(0)

  useEffect(() => {
    let current = true
    versionsApi.list(kind).then(
      (result) => {
        if (!current) return
        setVersions(result)
        setError(null)
      },
      (problem: unknown) => {
        if (current) setError(problem)
      },
    )
    return () => {
      current = false
    }
  }, [kind, token])

  const reload = useCallback(() => setToken((count) => count + 1), [])
  return { versions, error, reload }
}

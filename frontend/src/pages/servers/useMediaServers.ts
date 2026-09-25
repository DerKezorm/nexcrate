import { useCallback, useEffect, useState } from 'react'

import { mediaServersApi } from '../../api/mediaServers'
import type { MediaServer } from '../../api/types'

/** Die eingetragenen Medienserver, mit `reload` nach jeder Aenderung. */
export function useMediaServers() {
  const [servers, setServers] = useState<MediaServer[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [token, setToken] = useState(0)

  useEffect(() => {
    let current = true
    mediaServersApi.list().then(
      (result) => {
        if (!current) return
        setServers(result)
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
  return { servers, error, reload }
}

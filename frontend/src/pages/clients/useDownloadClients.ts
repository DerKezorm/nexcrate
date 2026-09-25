import { useCallback, useEffect, useState } from 'react'

import { downloadClientsApi } from '../../api/downloadClients'
import type { DownloadClient } from '../../api/types'

/** Die eingetragenen Download-Programme, mit `reload` nach jeder Aenderung. */
export function useDownloadClients() {
  const [clients, setClients] = useState<DownloadClient[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [token, setToken] = useState(0)

  useEffect(() => {
    let current = true
    downloadClientsApi.list().then(
      (result) => {
        if (!current) return
        setClients(result)
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
  return { clients, error, reload }
}

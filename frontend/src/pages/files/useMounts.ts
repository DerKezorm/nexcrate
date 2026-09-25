import { useEffect, useState } from 'react'

import { foldersApi } from '../../api/folders'
import type { FolderMount } from '../../api/types'

/** Die Ordner, die im Container von nexcrate eingebunden sind. Leer, wenn nexcrate keinen sieht. */
export function useMounts() {
  const [mounts, setMounts] = useState<FolderMount[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let current = true
    foldersApi.mounts().then(
      (result) => {
        if (!current) return
        setMounts(Array.isArray(result.mounts) ? result.mounts : [])
        setError(null)
      },
      (problem: unknown) => {
        if (current) setError(problem)
      },
    )
    return () => {
      current = false
    }
  }, [])

  return { mounts, error }
}

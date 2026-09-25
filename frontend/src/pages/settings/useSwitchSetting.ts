import { useCallback, useEffect, useState } from 'react'

/** Lesen und speichern eines Schalters, wie `GET` und `PUT` mit `{enabled}`. Muss stabil sein, etwa ein Objekt aus `api/`. */
export type SwitchSource = {
  get: () => Promise<{ enabled: boolean }>
  save: (enabled: boolean) => Promise<{ enabled: boolean }>
}

export type SwitchSetting = {
  /** null, solange der Wert nicht geladen ist. */
  enabled: boolean | null
  loadError: unknown
  busy: boolean
  problem: unknown
  /** Speichert und gibt zurueck, was der Server nun sagt, oder null, wenn es nicht ging. */
  change: (next: boolean) => Promise<boolean | null>
}

/**
 * Ein Schalter, der sofort speichert, wie "Automatisch suchen und laden" und "Untertitel mit ablegen". Er zeigt, was der
 * Server sagt, nicht was geklickt wurde: Lehnt der Server ab, bleibt der alte Stand stehen und der Grund darunter.
 */
export function useSwitchSetting(source: SwitchSource): SwitchSetting {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  useEffect(() => {
    let current = true
    source.get().then(
      (result) => {
        if (current) setEnabled(result.enabled === true)
      },
      (error: unknown) => {
        if (current) setLoadError(error)
      },
    )
    return () => {
      current = false
    }
  }, [source])

  const change = useCallback(
    async (next: boolean) => {
      setBusy(true)
      setProblem(null)
      try {
        const saved = (await source.save(next)).enabled === true
        setEnabled(saved)
        return saved
      } catch (error) {
        setProblem(error)
        return null
      } finally {
        setBusy(false)
      }
    },
    [source],
  )

  return { enabled, loadError, busy, problem, change }
}

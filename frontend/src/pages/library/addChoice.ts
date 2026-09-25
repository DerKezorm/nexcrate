/**
 * Welche Fassungen beim letzten Hinzufuegen gewaehlt waren, als Nummern. Beim ersten Mal steht
 * nichts da, dann ist nichts vorausgewaehlt.
 *
 * ⚠️ Jeder Zugriff in try/catch: Im privaten Modus oder bei gesperrtem Speicher wirft schon
 * `localStorage.getItem`. Dann gilt die Wahl nur fuer diesen Dialog.
 */
export const ADD_VERSIONS_KEY = 'nexcrate.addVersions'

export function readAddChoice(): number[] | null {
  try {
    const raw = localStorage.getItem(ADD_VERSIONS_KEY)
    if (raw === null) return null
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return null
    return parsed.filter((value): value is number => Number.isInteger(value))
  } catch {
    return null
  }
}

export function storeAddChoice(ids: readonly number[]): void {
  try {
    localStorage.setItem(ADD_VERSIONS_KEY, JSON.stringify([...ids]))
  } catch {
    // Ohne Speicher gilt die Wahl nur bis zum Schliessen.
  }
}

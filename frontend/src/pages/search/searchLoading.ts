import { createContext } from 'react'

import type { Download, SearchVersion } from '../../api/types'

/** Was in dieser Suche geladen wurde, je Fassung: das Release und der Download dazu. */
export type LoadedRelease = { releaseKey: string; download: Download }

/**
 * Was jeder Knopf "Laden" einer fertigen Suche braucht. Steht in einem Kontext, weil die Karte und die
 * Zeilen derselben Fassung voneinander wissen muessen: Nach dem Laden laeuft fuer die Fassung ein
 * Download, und jeder weitere Knopf dieser Fassung sagt das, statt es noch einmal zu versuchen.
 */
export type SearchLoading = {
  searchId: string
  versions: readonly SearchVersion[]
  loaded: ReadonlyMap<number, LoadedRelease>
  /** Seit S4: jedes geladene Release je Fassung als `version:release`. Eine Serienfassung laedt mehrere. */
  loadedKeys?: ReadonlySet<string>
  onLoaded: (versionId: number, releaseKey: string, download: Download) => void
}

export const SearchLoadingContext = createContext<SearchLoading | null>(null)

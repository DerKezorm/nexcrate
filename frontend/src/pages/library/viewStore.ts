export const LIBRARY_VIEWS = ['grid', 'list'] as const

export type LibraryView = (typeof LIBRARY_VIEWS)[number]

/** Unter diesem Namen merkt sich der Browser die Ansicht der Bibliothek. */
export const LIBRARY_VIEW_KEY = 'nexcrate.libraryView'

/**
 * Die gemerkte Ansicht. Ohne Speicher (privater Modus, gesperrte Website-Daten)
 * wirft schon der Zugriff; dann gilt das Raster.
 */
export function storedLibraryView(): LibraryView {
  try {
    return localStorage.getItem(LIBRARY_VIEW_KEY) === 'list' ? 'list' : 'grid'
  } catch {
    return 'grid'
  }
}

export function storeLibraryView(view: LibraryView): void {
  try {
    localStorage.setItem(LIBRARY_VIEW_KEY, view)
  } catch {
    // Dann gilt die Wahl nur, bis die Seite neu laedt.
  }
}

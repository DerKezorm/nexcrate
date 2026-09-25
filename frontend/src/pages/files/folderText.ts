import type { FolderMount } from '../../api/types'

/** Unter zehn Prozent frei wird die Belegung rosa. */
export const LOW_FREE_SHARE = 0.1

/** Der eingebundene Ordner, in dem ein Pfad liegt: der laengste passende. null, wenn keiner passt. */
export function mountOf(path: string, mounts: readonly FolderMount[]): FolderMount | null {
  let best: FolderMount | null = null
  for (const mount of mounts) {
    const base = mount.path.endsWith('/') ? mount.path : `${mount.path}/`
    const inside = path === mount.path || path.startsWith(base)
    if (inside && (best === null || mount.path.length > best.path.length)) best = mount
  }
  return best
}

/** Belegter Anteil von 0 bis 1. Ohne Gesamtgroesse 0. */
export function usedShare(freeBytes: number, totalBytes: number): number {
  if (!(totalBytes > 0)) return 0
  return Math.min(1, Math.max(0, 1 - freeBytes / totalBytes))
}

export function isLow(freeBytes: number, totalBytes: number): boolean {
  return totalBytes > 0 && freeBytes / totalBytes < LOW_FREE_SHARE
}

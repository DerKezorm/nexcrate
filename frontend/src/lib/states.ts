import type { VersionState } from '../api/types'
import type { SymbolName } from '../components/Symbol'

/**
 * Wie ein Zustand aussieht, an einer Stelle. Gruen vorhanden, Blau laeuft,
 * gestrichelt grau gesucht, grau durchgezogen nicht ueberwacht, Rosa Problem,
 * Bernstein "geht besser". Rosa gestrichelt: einem Album fehlen Titel.
 */
export const STATE_LOOK: Record<VersionState, { chip: string; text: string; symbol: SymbolName }> = {
  available: { chip: 'border-ok-500/40 bg-ok-500/10 text-ok-500', text: 'text-ok-500', symbol: 'check' },
  downloading: { chip: 'border-info-500/40 bg-info-500/10 text-info-500', text: 'text-info-500', symbol: 'download' },
  wanted: { chip: 'border-dashed border-ink-600 bg-ink-900 text-mist-500', text: 'text-mist-500', symbol: 'clock' },
  unmonitored: { chip: 'border-ink-600 bg-ink-850 text-mist-500', text: 'text-mist-500', symbol: 'eyeOff' },
  problem: { chip: 'border-bad-500/40 bg-bad-500/10 text-bad-500', text: 'text-bad-500', symbol: 'alert' },
  upgrade: { chip: 'border-accent-500/40 bg-accent-500/10 text-accent-400', text: 'text-accent-400', symbol: 'arrowUp' },
  incomplete: { chip: 'border-dashed border-bad-500/50 bg-bad-500/5 text-bad-500', text: 'text-bad-500', symbol: 'alert' },
}

/** Fortschritt vom Server (0 bis 100) als ganze Prozentzahl. Fehlt er, gilt 0. */
export function percentOf(progress: number | null | undefined): number {
  if (typeof progress !== 'number' || Number.isNaN(progress)) return 0
  return Math.round(Math.min(100, Math.max(0, progress)))
}

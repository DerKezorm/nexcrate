export type PatternField = 'movie_folder' | 'movie_file'

/** Wo man zuletzt in einem Muster war. Dort setzt ein Klick auf einen Platzhalter ein. */
export type PatternCursor = { field: PatternField; position: number | null }

/** Setzt einen Platzhalter an der Stelle des Cursors ein, ohne Cursor ans Ende. Der Cursor steht danach hinter ihm. */
export function insertToken(value: string, position: number | null, token: string): { value: string; position: number } {
  const at = position === null ? value.length : Math.min(position, value.length)
  return { value: value.slice(0, at) + token + value.slice(at), position: at + token.length }
}

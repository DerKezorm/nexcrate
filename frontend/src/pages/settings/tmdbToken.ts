/** Ein API Key von TMDB hat 32 Zeichen aus 0 bis 9 und a bis f. Der Token ist viel laenger. */
export function looksLikeApiKey(value: string): boolean {
  return /^[0-9a-f]{32}$/i.test(value)
}

/** Was eingefuegt wurde, ohne Leerraum am Rand und ohne ein mitkopiertes "Bearer ". */
export function cleanToken(value: string): string {
  return value
    .trim()
    .replace(/^bearer\s+/i, '')
    .trim()
}

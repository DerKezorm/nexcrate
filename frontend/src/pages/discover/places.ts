/**
 * Laender und Sprachen fuer die Filter von Entdecken. Die Namen kommen aus dem Browser (`Intl.DisplayNames`) in der
 * Sprache der Oberflaeche; fehlt einer, steht der Code da. Die Auswahl ist bewusst kurz: die Laender und Sprachen,
 * aus denen die meisten Filme und Serien bei TMDB kommen.
 */

export const COUNTRIES = [
  'AR', 'AT', 'AU', 'BE', 'BR', 'CA', 'CH', 'CN', 'CZ', 'DE', 'DK', 'ES', 'FI', 'FR', 'GB', 'IE', 'IN', 'IT', 'JP',
  'KR', 'MX', 'NL', 'NO', 'NZ', 'PL', 'PT', 'SE', 'TR', 'US', 'ZA',
] as const

export const LANGUAGES = ['da', 'de', 'en', 'es', 'fi', 'fr', 'hi', 'it', 'ja', 'ko', 'nl', 'no', 'pl', 'pt', 'sv', 'tr', 'zh'] as const

function namer(language: string, type: 'region' | 'language'): (code: string) => string {
  try {
    const names = new Intl.DisplayNames([language], { type })
    return (code) => names.of(code) ?? code
  } catch {
    return (code) => code
  }
}

/** Die Codes mit Namen, nach Namen sortiert. */
export function named(codes: readonly string[], language: string, type: 'region' | 'language'): { code: string; name: string }[] {
  const name = namer(language, type)
  return codes
    .map((code) => ({ code, name: name(code) }))
    .sort((a, b) => a.name.localeCompare(b.name, language))
}

/**
 * Laendernamen aus einem ISO-3166-Code, ueber das eingebaute `Intl.DisplayNames`: Keine eigene Liste zu pflegen, und
 * jede Sprache, die der Browser kennt. MusicBrainz nutzt dazu zwei erfundene Kennungen (Musik M1, Entscheidung 28),
 * die bekommen ihren Namen von Hand.
 */
const SPECIAL: Record<string, { de: string; en: string }> = {
  XE: { de: 'Europa', en: 'Europe' },
  XW: { de: 'Weltweit', en: 'Worldwide' },
}

export function countryText(code: string | null | undefined, language: string): string | null {
  if (!code) return null
  const upper = code.trim().toUpperCase()
  if (upper === '') return null
  const special = SPECIAL[upper]
  if (special) return special[language.slice(0, 2).toLowerCase() === 'de' ? 'de' : 'en']
  try {
    const names = new Intl.DisplayNames([language], { type: 'region' })
    return names.of(upper) ?? upper
  } catch {
    return upper
  }
}

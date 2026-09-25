import type { ExpertProfileBody, LanguageEntry, ProfileQualityEntry } from '../../api/types'

/**
 * Die Qualitaetenliste eines Profils von Hand. Sie hat die Form, die die Regeln haben und die Bewertung liest
 * (`services/releases/decision.py`): eine einzelne Qualitaet `{name, allowed}` oder eine Gruppe gleichwertiger
 * `{group, items, allowed}`. Die Reihenfolge geht von der schwaechsten zur besten, und der Cutoff nennt den
 * Namen eines Eintrags.
 */
export type QualityEntry = ProfileQualityEntry

/** Wie der Eintrag heisst: der Name der Gruppe oder der der Qualitaet. Der Cutoff zeigt genau darauf. */
export function entryLabel(entry: QualityEntry): string {
  return entry.group ?? entry.name ?? ''
}

export function entryItems(entry: QualityEntry): string[] {
  return entry.items ?? (entry.name === undefined ? [] : [entry.name])
}

/** Der Name einer neuen Gruppe: die Mitglieder, gleichwertig gelesen. Der Server nimmt hoechstens 64 Zeichen. */
export function groupLabel(items: string[]): string {
  return items.join(' = ').slice(0, 64)
}

export function moved(entries: QualityEntry[], index: number, direction: -1 | 1): QualityEntry[] {
  const target = index + direction
  if (target < 0 || target >= entries.length) return entries
  const next = [...entries]
  const moving = next[index]
  next[index] = next[target]
  next[target] = moving
  return next
}

/** Ein Eintrag wandert an die Stelle eines anderen, alle dazwischen ruecken auf: so legt Ziehen ab. */
export function placed(entries: QualityEntry[], from: number, to: number): QualityEntry[] {
  if (from === to || from < 0 || to < 0 || from >= entries.length || to >= entries.length) return entries
  const next = [...entries]
  const [moving] = next.splice(from, 1)
  next.splice(to, 0, moving)
  return next
}

/** Zwei benachbarte Eintraege werden eine Gruppe gleichwertiger Qualitaeten, wie 720p und 1080p bei TRaSH. */
export function merged(entries: QualityEntry[], index: number): QualityEntry[] {
  if (index < 0 || index + 1 >= entries.length) return entries
  const items = [...entryItems(entries[index]), ...entryItems(entries[index + 1])]
  const group: QualityEntry = { group: groupLabel(items), items, allowed: entries[index].allowed || entries[index + 1].allowed }
  return [...entries.slice(0, index), group, ...entries.slice(index + 2)]
}

/** Eine Gruppe wieder in ihre Qualitaeten zerlegen, in derselben Reihenfolge und mit demselben Haken. */
export function split(entries: QualityEntry[], index: number): QualityEntry[] {
  const entry = entries[index]
  if (entry === undefined || entry.group === undefined) return entries
  const parts = entryItems(entry).map((name) => ({ name, allowed: entry.allowed }))
  return [...entries.slice(0, index), ...parts, ...entries.slice(index + 1)]
}

/** Nach jeder Aenderung: der Cutoff muss auf einen Eintrag zeigen, der genommen wird. Sonst der hoechste. */
export function fixedCutoff(entries: QualityEntry[], cutoff: string | null): string | null {
  const allowed = entries.filter((entry) => entry.allowed).map(entryLabel)
  if (cutoff !== null && allowed.includes(cutoff)) return cutoff
  return allowed.length === 0 ? null : allowed[allowed.length - 1]
}

/** Die Qualitaeten, die noch in keinem Eintrag stehen, in der Reihenfolge des Servers. */
export function missingQualities(entries: QualityEntry[], available: string[]): string[] {
  const inside = new Set(entries.flatMap(entryItems))
  return available.filter((name) => !inside.has(name))
}

/**
 * Was gespeichert wird. Ein Format ohne Punkte bleibt weg: Es aendert nichts an der Bewertung, und ohne Verweis
 * darauf laesst es sich auf seiner Seite wieder entfernen.
 */
/** Was im Sprachfeld steht, wenn mehrere Sprachen Pflicht sind: nur der Assistent setzt das, und es bleibt, bis man waehlt. */
export const SEVERAL_LANGUAGES = '*'

/**
 * Radarrs Profil hat genau eine Sprache: beliebig, die Originalsprache oder eine bestimmte. Hier ist das die eine
 * Pflichtsprache der Liste. `''` heisst beliebig, `SEVERAL_LANGUAGES` mehrere Pflichtsprachen aus dem Assistenten.
 */
export function requiredLanguage(languages: LanguageEntry[]): string {
  const required = languages.filter((entry) => entry.role === 'required')
  if (required.length === 0) return ''
  return required.length === 1 ? required[0].code : SEVERAL_LANGUAGES
}

/**
 * Die Liste mit dieser einen Pflichtsprache. Was der Assistent als "gern dazu" eingetragen hat, bleibt stehen:
 * von Hand zaehlt es nicht (die Punkte dafuer stehen bei den Formaten), aber es soll hier nicht still verschwinden.
 */
export function withRequiredLanguage(languages: LanguageEntry[], code: string): LanguageEntry[] {
  if (code === SEVERAL_LANGUAGES) return languages
  const others = languages.filter((entry) => entry.role !== 'required' && entry.code !== code)
  return code === '' ? others : [{ code, role: 'required' }, ...others]
}

export function bodyToSave(profile: ExpertProfileBody, entries: QualityEntry[], cutoff: string | null, scores: Record<number, number>): ExpertProfileBody {
  return {
    ...profile,
    qualities: entries,
    cutoff,
    formats: Object.entries(scores)
      .map(([id, score]) => ({ format_id: Number(id), score }))
      .filter((entry) => entry.score !== 0)
      .sort((left, right) => left.format_id - right.format_id),
  }
}

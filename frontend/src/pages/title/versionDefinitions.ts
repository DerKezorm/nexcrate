import type { TFunction } from 'i18next'

import type { Subtitle, TitleVersion, Version } from '../../api/types'
import { multiLanguageName } from '../indexers/searchSettings'

/** Die Untertitel einer Fassung (Schritt 3c), soweit lesbar. Ein Server von davor schickt keine. */
export function subtitlesOf(version: TitleVersion): Subtitle[] {
  return Array.isArray(version.subtitles) ? version.subtitles.filter((entry) => entry !== null && typeof entry === 'object') : []
}

/** Eine Untertitel-Datei in Worten: "Englisch, erzwungen" oder "Sprache unbekannt, für Hörgeschädigte". */
export function subtitleText(t: TFunction, subtitle: Subtitle, language: string): string {
  const code = typeof subtitle.language === 'string' ? subtitle.language.trim().toLowerCase() : ''
  const name = code !== '' ? multiLanguageName(t, code, language) : t('title.version.subtitleUnknown')
  const parts = [name, subtitle.forced === true ? t('title.version.subtitleForced') : null, subtitle.sdh === true ? t('title.version.subtitleSdh') : null]
  return parts.filter((part): part is string => part !== null).join(', ')
}

/** Nur der Dateiname, ohne Ordner davor. null ohne lesbaren Namen. */
export function fileNameOf(path: unknown): string | null {
  if (typeof path !== 'string') return null
  const name = path.split(/[\\/]/).filter((part) => part !== '').at(-1)
  return name ?? null
}

/**
 * Die Nummer der Fassung unter Einstellungen zu einer Fassung am Titel. `PATCH` erwartet diese.
 *
 * ⚠️ `TitleDetail.versions[].id` ist die Nummer am Titel, nicht die unter Einstellungen. Solange
 * der Server `version_id` nicht mitschickt, gleicht die Seite ueber den Namen ab; den gibt es je
 * Art nur einmal (409 `version_label_taken`).
 */
export function definitionOf(version: TitleVersion, definitions: readonly Version[]): number | null {
  if (typeof version.version_id === 'number') return version.version_id
  return definitions.find((definition) => definition.label === version.label)?.id ?? null
}

/** Eine Verbindung zu Radarr liefert diese Fassung. Entfernen kann man sie hier nicht. */
export function isFromSource(version: TitleVersion): boolean {
  return typeof version.source_name === 'string' && version.source_name !== ''
}

/**
 * Wie viele Downloads dieser Fassung noch im Download-Programm sind und nicht abgelegt (seit Schritt 3, Aenderung B).
 * Genau die zaehlt der Server fuer `remove_downloads`, Hinweise wie `dangerous_file` eingeschlossen. `download` zeigt
 * nur den Download, der das Laden sperrt; wer danach entschied, lief bei einem Hinweis in das 409.
 */
export function pendingDownloads(version: TitleVersion): number {
  const count = version.pending_downloads
  return typeof count === 'number' && Number.isInteger(count) && count > 0 ? count : 0
}

/**
 * Was der Server in einem 409 `title_download_active` oder `version_download_active` nennt, damit sich der Dialog
 * das merkt. Ein Sicherheitsnetz fuer einen Download, der erst nach dem Oeffnen begonnen hat: Der Dialog laedt den
 * Titel nach, aber bis die Antwort da ist, oder wenn sie ausbleibt, weiss er es nur aus dem 409.
 */
export function namedCount(value: unknown): number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : 1
}

/** Die Fassung aus `values.label`; ohne Namen alle, die gerade gehen sollen. */
export function namedLabels(value: unknown, removing: readonly string[]): string[] {
  return typeof value === 'string' && value !== '' ? [value] : [...removing]
}

/** Woher die Fassung kommt: "Aus Radarr: {name}" oder "Von dir hinzugefügt". */
export function originText(t: TFunction, version: TitleVersion): string {
  if (isFromSource(version)) {
    const name = version.source_name ?? ''
    // Selbst angelegt, dann hat ein Import den Platz uebernommen. Der Verlauf bleibt.
    return version.added_by === 'owner' ? t('title.origin.ownerFed', { name }) : t('title.origin.import', { name })
  }
  return version.added_by === 'import' ? t('title.origin.importGone') : t('title.origin.owner')
}

/**
 * What the profile still wants, in words (decision 20). A group of qualities shows its qualities, never its inner name
 * such as "Merged QPs": members of one resolution read "Bluray, WEBRip oder WEBDL 1080p", others are listed whole.
 * Without members the name stays as the server sends it; null without a target.
 */
export function targetText(version: TitleVersion, language: string): string | null {
  const items = Array.isArray(version.upgrade_to_items) ? version.upgrade_to_items.filter((item): item is string => typeof item === 'string' && item !== '') : []
  if (items.length === 0) return typeof version.upgrade_to === 'string' && version.upgrade_to !== '' ? version.upgrade_to : null
  if (items.length === 1) return items[0]
  const parts = items.map((item) => /^(.+)-(\d+p)$/.exec(item))
  const resolution = parts[0]?.[2]
  const either = new Intl.ListFormat(language, { type: 'disjunction' })
  if (resolution !== undefined && parts.every((part) => part?.[2] === resolution)) {
    return `${either.format(parts.map((part) => part?.[1] ?? ''))} ${resolution}`
  }
  return either.format(items)
}

/**
 * The two numbers of an upgrade by score (decision 20): the file's score and the profile's upgrade-until. null for any
 * other reason, for a version a source feeds, and when a number is missing, as from a server before decision 20.
 */
export function scoreUpgradeOf(version: TitleVersion): { score: number; until: number } | null {
  if (isFromSource(version) || version.upgrade_reason !== 'score') return null
  const score = version.current_score
  const until = version.upgrade_until
  if (typeof score !== 'number' || !Number.isFinite(score) || typeof until !== 'number' || !Number.isFinite(until)) return null
  return { score, until }
}

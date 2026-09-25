import type { DelayRule, TaggedDelayRule } from '../../api/types'

/** Eine Regel fuer Titel mit Tags im Entwurf: Tags und Minuten als Text, wie getippt. */
export type TaggedDraft = { rule: DelayRule; tags: string; usenet: string; torrent: string }

export function draftOf(tagged: TaggedDelayRule): TaggedDraft {
  const rule = ruleOnly(tagged)
  const tags = tagged.tags
  return { rule, tags: tags.join(', '), usenet: String(rule.usenet_minutes), torrent: String(rule.torrent_minutes) }
}

/** Die Namen aus dem Feld: durch Komma getrennt, leere weg. */
export function tagsOf(text: string): string[] {
  return text
    .split(',')
    .map((part) => part.trim())
    .filter((part) => part !== '')
}

/** Eine Regel ohne `tagged` und ohne `tags`: was eine Fassung oder eine Regel fuer Tags selbst festlegt. */
export function ruleOnly(value: DelayRule & { tagged?: unknown; tags?: unknown }): DelayRule {
  const { tagged: _tagged, tags: _tags, ...rule } = value
  return rule
}

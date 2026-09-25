import { useId, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../api/client'
import { useTagNames } from './useTagNames'
import { Symbol } from './Symbol'
import { Button, FormMessage } from './ui'

/** Tags als Marken; eine Marke ohne Knopf, wenn nichts zu aendern ist. */
export function TagChips({ tags, onRemove, busy = false }: { tags: string[]; onRemove?: (tag: string) => void; busy?: boolean }) {
  const { t } = useTranslation()
  if (tags.length === 0) return null
  return (
    <ul className="flex flex-wrap gap-2" aria-label={t('tags.label')}>
      {tags.map((tag) => (
        <li key={tag} className="inline-flex items-center gap-1 rounded-full border border-ink-600 bg-ink-900 px-2.5 py-1 text-xs text-mist-200">
          <Symbol name="tag" className="h-3.5 w-3.5 text-mist-500" />
          {tag}
          {onRemove && (
            <button
              type="button"
              disabled={busy}
              onClick={() => onRemove(tag)}
              aria-label={t('tags.remove', { tag })}
              className="ml-0.5 rounded-full text-mist-500 hover:text-bad-500 disabled:opacity-50"
            >
              <Symbol name="close" className="h-3.5 w-3.5" />
            </button>
          )}
        </li>
      ))}
    </ul>
  )
}

/**
 * Tags eines Films, einer Serie oder eines Kuenstlers: Marken mit Entfernen, dazu ein Feld mit
 * Vorschlaegen. Gespeichert wird bei jedem Hinzufuegen und Entfernen; der Server gibt die Tags so zurueck, wie er sie
 * speichert (klein, sortiert). Ohne `onSave`: ein Album zeigt die Tags seines Kuenstlers, geaendert werden sie dort.
 * `tags` gilt beim ersten Zeigen; fuer einen anderen Titel setzt die Seite die Komponente ueber `key` neu auf.
 */
export function TagEditor({ tags, onSave, readOnlyHint }: { tags: string[]; onSave?: (next: string[]) => Promise<string[]>; readOnlyHint?: string }) {
  const { t } = useTranslation()
  const listId = useId()
  const [wanted, setWanted] = useState(false)
  const names = useTagNames(wanted)
  const [current, setCurrent] = useState(tags)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)


  async function save(next: string[]) {
    if (!onSave) return
    setBusy(true)
    setProblem(null)
    try {
      setCurrent(await onSave(next))
      setText('')
    } catch (error) {
      setProblem(error)
    } finally {
      setBusy(false)
    }
  }

  function add(event: FormEvent) {
    event.preventDefault()
    const name = text.trim()
    if (name === '' || busy) return
    void save([...current, name])
  }

  return (
    <div className="flex flex-col gap-2">
      <TagChips tags={current} onRemove={onSave ? (tag) => void save(current.filter((item) => item !== tag)) : undefined} busy={busy} />
      {onSave ? (
        <form onSubmit={add} className="flex max-w-md items-center gap-2">
          <input
            value={text}
            onChange={(event) => setText(event.target.value)}
            onFocus={() => setWanted(true)}
            list={listId}
            maxLength={64}
            placeholder={t('tags.add')}
            aria-label={t('tags.add')}
            className="min-w-0 flex-1 rounded-full border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm text-mist-100 placeholder:text-mist-600 focus:border-accent-500 focus:outline-none"
          />
          <datalist id={listId}>
            {names
              .filter((name) => !current.includes(name))
              .map((name) => (
                <option key={name} value={name} />
              ))}
          </datalist>
          <Button type="submit" variant="ghost" size="sm" loading={busy} disabled={text.trim() === ''}>
            <Symbol name="plus" />
            {t('tags.addButton')}
          </Button>
        </form>
      ) : (
        readOnlyHint && <p className="text-xs text-mist-500">{readOnlyHint}</p>
      )}
      {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
    </div>
  )
}

/** Der Filter "Tag" der Bibliothek: alle Tags, oder einer. */
export function TagFilter({ value, onChange }: { value: string; onChange: (next: string) => void }) {
  const { t } = useTranslation()
  const names = useTagNames()
  if (names.length === 0 && value === '') return null
  return (
    <label className="flex items-center gap-2 text-sm text-mist-500">
      <Symbol name="tag" className="h-4 w-4" />
      <span className="sr-only">{t('tags.filter')}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-label={t('tags.filter')}
        className="rounded-full border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
      >
        <option value="">{t('tags.all')}</option>
        {[...new Set([...names, ...(value ? [value] : [])])].map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
    </label>
  )
}

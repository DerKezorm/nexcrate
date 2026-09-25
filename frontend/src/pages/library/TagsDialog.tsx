import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { tagsApi, type TagsChange } from '../../api/tags'
import { Dialog } from '../../components/Dialog'
import { useTagNames } from '../../components/useTagNames'
import { Button, FormMessage } from '../../components/ui'
import { formatNumber } from '../../lib/format'

function names(text: string): string[] {
  return text
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item !== '')
}

/**
 * "Tags" fuer die Auswahl: hinzufuegen und entfernen, durch Komma getrennt. Was betroffen ist,
 * sagt die Leiste darueber; der Dialog schickt dieselbe Auswahl mit.
 */
export function TagsDialog({ scope, count, onClose, onDone }: { scope: Omit<TagsChange, 'add' | 'remove'>; count: number; onClose: () => void; onDone: (changed: number) => void }) {
  const { t, i18n } = useTranslation()
  const listId = useId()
  const known = useTagNames()
  const [add, setAdd] = useState('')
  const [remove, setRemove] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const nothing = names(add).length === 0 && names(remove).length === 0

  async function apply() {
    setBusy(true)
    setProblem(null)
    try {
      const result = await tagsApi.change({ ...scope, add: names(add), remove: names(remove) })
      onDone(result.changed)
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  const field = 'w-full rounded-xl border border-ink-700 bg-ink-900 px-3 py-2 text-sm text-mist-100 focus:border-accent-500 focus:outline-none'
  return (
    <Dialog
      open
      title={t('tags.dialog.title', { count, value: formatNumber(count, i18n.language) })}
      onClose={() => !busy && onClose()}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void apply()} loading={busy} disabled={nothing}>
            {t('tags.dialog.apply')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5 text-sm font-medium text-mist-300">
          {t('tags.dialog.add')}
          <input value={add} onChange={(event) => setAdd(event.target.value)} list={listId} className={field} />
        </label>
        <label className="flex flex-col gap-1.5 text-sm font-medium text-mist-300">
          {t('tags.dialog.remove')}
          <input value={remove} onChange={(event) => setRemove(event.target.value)} list={listId} className={field} />
        </label>
        <datalist id={listId}>
          {known.map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
        <p className="text-xs text-mist-500">{t('tags.dialog.hint')}</p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

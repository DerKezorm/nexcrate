import { useTranslation } from 'react-i18next'

import { TagEditor } from '../../components/TagEditor'

/**
 * Tags an einem Indexer oder Download-Programm, gespeichert bei jedem Hinzufuegen und
 * Entfernen wie an einem Titel. `hint` sagt, was sie dort bewirken.
 */
export function CardTags({ id, tags, hint, onSave }: { id: number; tags: string[]; hint: string; onSave: (next: string[]) => Promise<string[]> }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-1.5">
      <h4 className="text-xs font-semibold text-mist-400">{t('settings.tagField.field')}</h4>
      <p className="text-xs text-mist-500">{hint}</p>
      <TagEditor key={id} tags={tags} onSave={onSave} />
    </div>
  )
}

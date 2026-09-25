import { useTranslation } from 'react-i18next'

import type { RenameUnit } from '../../api/rename'
import { Symbol } from '../../components/Symbol'
import { noteText, skipText } from './renameText'

/**
 * "vorher, nachher" einer Einheit: erst die Ordner, dann die Dateien. Der alte Name durchgestrichen und gedimmt, der
 * neue darunter. Hinweise und ein Grund zum Auslassen stehen oben.
 */
export function RenameSteps({ unit }: { unit: RenameUnit }) {
  const { t } = useTranslation()
  const notes = unit.notes.map((note) => noteText(t, note)).filter((text): text is string => text !== null)
  return (
    <div className="flex flex-col gap-2">
      {unit.skip !== null && (
        <p className="flex items-start gap-2 text-sm text-bad-500">
          <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
          {skipText(t, unit.skip, unit.skip_values)}
        </p>
      )}
      {notes.map((text) => (
        <p key={text} className="flex items-start gap-2 text-sm text-mist-400">
          <Symbol name="info" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
          {text}
        </p>
      ))}
      {unit.left > 0 && <p className="text-sm text-mist-400">{t('settings.rename.unit.left', { count: unit.left })}</p>}
      <ul className="flex flex-col gap-2" aria-label={t('settings.rename.unit.stepsLabel', { name: unit.name })}>
        {unit.folders.map((folder) => (
          <li key={`folder-${folder.old}-${folder.new}`} className="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-x-2 text-sm">
            <Symbol name="folder" className="mt-0.5 h-4 w-4 text-accent-400" />
            <div className="min-w-0">
              <p className="wrap-anywhere text-mist-500 line-through">{folder.old || t('settings.rename.unit.rootFolder')}</p>
              <p className="wrap-anywhere text-mist-100">{folder.new}</p>
            </div>
          </li>
        ))}
        {unit.steps.map((step) => (
          <li key={`step-${step.old}`} className="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-x-2 text-sm">
            <Symbol name={step.what === 'other' ? 'layers' : 'note'} className="mt-0.5 h-4 w-4 text-mist-600" />
            <div className="min-w-0">
              <p className="wrap-anywhere text-mist-500 line-through">{step.old}</p>
              <p className="wrap-anywhere text-mist-100">{step.new}</p>
            </div>
          </li>
        ))}
      </ul>
      {unit.more > 0 && <p className="text-xs text-mist-500">{t('settings.rename.unit.more', { count: unit.more })}</p>}
    </div>
  )
}

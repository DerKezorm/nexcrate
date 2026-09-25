import type { TFunction } from 'i18next'

import type { RenameNote, RenameUnit } from '../../api/rename'

/** Warum eine Einheit ausgelassen wird, als Satz. Unbekannte Codes sagen es allgemein. */
export function skipText(t: TFunction, code: string, values: Record<string, unknown> = {}): string {
  const name = typeof values.name === 'string' ? values.name : ''
  switch (code) {
    case 'folder_missing':
      return t('settings.rename.skip.folder_missing')
    case 'file_missing':
      return t('settings.rename.skip.file_missing')
    case 'busy':
      return t('settings.rename.skip.busy')
    case 'link':
      return t('settings.rename.skip.link')
    case 'target_taken':
      return t('settings.rename.skip.target_taken', { name })
    case 'target_twice':
      return t('settings.rename.skip.target_twice', { name })
    case 'target_planned':
      return t('settings.rename.skip.target_planned', { name })
    case 'several_roots':
      return t('settings.rename.skip.several_roots')
    case 'file_twice':
      return t('settings.rename.skip.file_twice')
    case 'move_failed':
      return t('settings.rename.skip.move_failed')
    case 'database':
      return t('settings.rename.skip.database')
    case 'changed':
      return t('settings.rename.skip.changed')
    case 'files_moved':
      return t('settings.rename.skip.files_moved')
    case 'unchanged':
      return t('settings.rename.skip.unchanged')
    default:
      return t('settings.rename.skip.other')
  }
}

/** Ein Hinweis zu einer Einheit, als Satz; null fuer einen, den die Seite nicht kennt. */
export function noteText(t: TFunction, note: RenameNote): string | null {
  switch (note.code) {
    case 'folder_shared':
      return t('settings.rename.note.folder_shared')
    case 'artist_folder_shared':
      return t('settings.rename.note.artist_folder_shared')
    case 'folder_split':
      return t('settings.rename.note.folder_split')
    case 'file_missing':
      return t('settings.rename.note.file_missing', { name: typeof note.values.name === 'string' ? note.values.name : '' })
    case 'facts_missing':
      return t('settings.rename.note.facts_missing')
    default:
      return null
  }
}

/** Die Zeile unter dem Namen: wie viele Dateien und Ordner, bei Musik wie viele Alben. */
export function unitSub(t: TFunction, unit: RenameUnit): string {
  const parts = [t('settings.rename.unit.files', { count: unit.files }), t('settings.rename.unit.folders', { count: unit.folders.length })]
  if (unit.kind === 'music') parts.unshift(t('settings.rename.unit.albums', { count: unit.albums.length }))
  return parts.join(' · ')
}

export function splits(unit: RenameUnit): boolean {
  return unit.notes.some((note) => note.code === 'folder_split')
}

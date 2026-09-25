import type { TFunction } from 'i18next'

import type { RadarrNote } from '../../api/types'

/** Die Hinweise zu Radarrs Benennung, getrennt nach dem Muster, zu dem sie gehoeren. Unbekannte Codes fallen weg. */
export type NamingNoteTexts = { folder: string[]; file: string[]; general: string[] }

/**
 * Was Radarr bei der Benennung anders macht, als Saetze. `radarr_no_rename` steht beim Dateimuster, `slash_in_pattern`
 * beim Muster aus `values.pattern`, der Rest unter beiden. Keiner sperrt die Uebernahme.
 */
export function namingNoteTexts(t: TFunction, notes: readonly RadarrNote[] | undefined): NamingNoteTexts {
  const texts: NamingNoteTexts = { folder: [], file: [], general: [] }
  for (const note of Array.isArray(notes) ? notes : []) {
    switch (note?.code) {
      case 'radarr_no_rename':
        texts.file.push(t('settings.files.radarrNaming.notes.radarr_no_rename'))
        break
      case 'slash_in_pattern': {
        const text = t('settings.files.radarrNaming.notes.slash_in_pattern')
        const pattern = note.values?.pattern
        if (pattern === 'movie_folder') texts.folder.push(text)
        else if (pattern === 'movie_file') texts.file.push(text)
        else texts.general.push(text)
        break
      }
      case 'colon_format':
        texts.general.push(t('settings.files.radarrNaming.notes.colon_format'))
        break
      case 'illegal_characters_kept':
        texts.general.push(t('settings.files.radarrNaming.notes.illegal_characters_kept'))
        break
    }
  }
  return texts
}

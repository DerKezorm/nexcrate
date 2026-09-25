/**
 * Die Saetze zu den Tags eines Albums (M4, Entscheidungen 29 und 30). Woertliche Schluessel, damit `keys.test.ts` sie
 * sieht.
 */

import type { TFunction } from 'i18next'

/** Woher die Tags einer abgelegten Datei stammen. */
export function tagStateText(t: TFunction, state: string | null | undefined): string | null {
  switch (state) {
    case 'download':
      return t('title.album.tags.state.download')
    case 'written':
      return t('title.album.tags.state.written')
    case 'linked':
      return t('title.album.tags.state.linked')
    case 'format':
      return t('title.album.tags.state.format')
    case 'off':
      return t('title.album.tags.state.off')
    case 'failed':
      return t('title.album.tags.state.failed')
    default:
      return null
  }
}

/** Warum eine Datei nicht geschrieben werden kann. */
export function tagRefusalText(t: TFunction, refusal: string): string {
  switch (refusal) {
    case 'linked':
      return t('title.album.tags.refusal.linked')
    case 'format':
      return t('title.album.tags.refusal.format')
    default:
      return t('title.album.tags.refusal.unreadable')
  }
}

/** Ein Feld nach Picards Namen in Worten; unbekannte bleiben, wie sie heissen. */
export function tagFieldText(t: TFunction, field: string): string {
  switch (field) {
    case 'title':
      return t('title.album.tags.field.title')
    case 'artist':
      return t('title.album.tags.field.artist')
    case 'artists':
      return t('title.album.tags.field.artists')
    case 'album':
      return t('title.album.tags.field.album')
    case 'albumartist':
      return t('title.album.tags.field.albumartist')
    case 'albumartistsort':
      return t('title.album.tags.field.albumartistsort')
    case 'tracknumber':
      return t('title.album.tags.field.tracknumber')
    case 'totaltracks':
      return t('title.album.tags.field.totaltracks')
    case 'discnumber':
      return t('title.album.tags.field.discnumber')
    case 'totaldiscs':
      return t('title.album.tags.field.totaldiscs')
    case 'date':
      return t('title.album.tags.field.date')
    case 'originaldate':
      return t('title.album.tags.field.originaldate')
    case 'releasecountry':
      return t('title.album.tags.field.releasecountry')
    case 'releasestatus':
      return t('title.album.tags.field.releasestatus')
    case 'releasetype':
      return t('title.album.tags.field.releasetype')
    case 'media':
      return t('title.album.tags.field.media')
    case 'label':
      return t('title.album.tags.field.label')
    case 'catalognumber':
      return t('title.album.tags.field.catalognumber')
    case 'barcode':
      return t('title.album.tags.field.barcode')
    case 'compilation':
      return t('title.album.tags.field.compilation')
    default:
      return field
  }
}

/** Wie nexcrate die Datei eines Downloads dem Titel zugeordnet hat. Woertliche Schluessel fuer `keys.test.ts`. */
export function viaText(t: TFunction, via: string | null | undefined): string | null {
  switch (via) {
    case 'id':
      return t('title.album.tracks.via.id')
    case 'position':
      return t('title.album.tracks.via.position')
    case 'name':
      return t('title.album.tracks.via.name')
    case 'fingerprint':
      return t('title.album.tracks.via.fingerprint')
    case 'owner':
      return t('title.album.tracks.via.owner')
    default:
      return null
  }
}

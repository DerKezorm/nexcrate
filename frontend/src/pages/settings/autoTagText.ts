import type { TFunction } from 'i18next'

import type { AutoTagCondition, AutoTagConditionType, AutoTagKind } from '../../api/autoTags'
import type { SourceApp } from '../../api/types'

/** Aus welcher App Regeln einer Art kommen. */
export const APP_OF_KIND: Record<AutoTagKind, SourceApp> = { movie: 'radarr', series: 'sonarr', album: 'lidarr' }
export const APP_NAME: Record<SourceApp, string> = { radarr: 'Radarr', sonarr: 'Sonarr', lidarr: 'Lidarr' }

/** Woertliche Schluessel, damit keys.test sie findet. */
export function conditionName(t: TFunction, type: AutoTagConditionType): string {
  switch (type) {
    case 'genre':
      return t('tags.auto.type.genre')
    case 'year':
      return t('tags.auto.type.year')
    case 'root_folder':
      return t('tags.auto.type.root_folder')
    case 'runtime':
      return t('tags.auto.type.runtime')
    case 'keyword':
      return t('tags.auto.type.keyword')
    case 'studio':
      return t('tags.auto.type.studio')
    case 'original_language':
      return t('tags.auto.type.original_language')
    case 'profile':
      return t('tags.auto.type.profile')
    case 'status':
      return t('tags.auto.type.status')
    case 'monitored':
      return t('tags.auto.type.monitored')
    case 'tag':
      return t('tags.auto.type.tag')
    case 'series_type':
      return t('tags.auto.type.series_type')
  }
}

export function statusName(t: TFunction, status: string): string {
  switch (status) {
    case 'tba':
      return t('tags.auto.status.tba')
    case 'announced':
      return t('tags.auto.status.announced')
    case 'in_cinemas':
      return t('tags.auto.status.in_cinemas')
    case 'released':
      return t('tags.auto.status.released')
    case 'continuing':
      return t('tags.auto.status.continuing')
    case 'ended':
      return t('tags.auto.status.ended')
    case 'upcoming':
      return t('tags.auto.status.upcoming')
    default:
      return status
  }
}

export function seriesTypeName(t: TFunction, type: string): string {
  switch (type) {
    case 'standard':
      return t('tags.auto.seriesType.standard')
    case 'daily':
      return t('tags.auto.seriesType.daily')
    case 'anime':
      return t('tags.auto.seriesType.anime')
    default:
      return type
  }
}

/** Eine Bedingung in einer Zeile, etwa "Genre: Animation, Komoedie" oder "nicht Jahr: 2000 bis 2010". */
export function conditionLine(t: TFunction, condition: AutoTagCondition, profiles: Map<number, string>): string {
  const name = conditionName(t, condition.type)
  let value: string
  if (condition.type === 'year' || condition.type === 'runtime') value = t('tags.auto.range', { min: condition.min, max: condition.max })
  else if (condition.values && condition.values.length > 0) value = condition.values.join(', ')
  else if (condition.type === 'profile') value = profiles.get(condition.profile_id ?? -1) ?? String(condition.profile_id ?? '')
  else if (condition.type === 'status') value = statusName(t, condition.value ?? '')
  else if (condition.type === 'series_type') value = seriesTypeName(t, condition.value ?? '')
  else value = condition.value ?? ''
  const text = value ? `${name}: ${value}` : name
  const marks = [condition.negate ? t('tags.auto.negateMark') : '', condition.required ? t('tags.auto.requiredMark') : ''].filter(Boolean)
  return marks.length > 0 ? `${text} (${marks.join(', ')})` : text
}

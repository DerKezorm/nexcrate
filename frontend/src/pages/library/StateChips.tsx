import { useTranslation } from 'react-i18next'

import type { LibraryStats, MediaKind } from '../../api/types'
import { formatNumber } from '../../lib/format'
import { stateFiltersFor, type StateFilter } from './address'

/**
 * Die Zustandsfilter der Bibliothek als Knoepfe, fuer Filme, Serien und Alben gleich. Jeder Knopf ausser "Alle" traegt
 * die Zahl der Titel, die er zeigt (`state_titles` aus `GET /api/library/stats`); ohne Titel steht keine Zahl da.
 * Problem rosa, unklare Dateien Bernstein, die uebrigen zurueckhaltend.
 */
export function StateChips({
  kind,
  value,
  stats,
  onChange,
}: {
  kind: MediaKind
  value: StateFilter
  stats: LibraryStats | null
  onChange: (next: StateFilter) => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language

  const label = (filter: StateFilter): string =>
    ({
      all: t('library.filter.allStates'),
      wanted: t('library.filter.wanted'),
      downloading: t('library.filter.downloading'),
      problem: t('library.filter.problem'),
      upgrade: t('library.filter.upgrade'),
      incomplete: t('library.filter.incomplete'),
      unmonitored: t('library.filter.unmonitored'),
      unclear: t('library.filter.unclear'),
    })[filter]

  const count = (filter: StateFilter): number => {
    if (stats === null || filter === 'all') return 0
    if (filter === 'unclear') return (kind === 'album' ? stats.unclear_albums : stats.unclear_titles) ?? 0
    // Aeltere Server ohne state_titles kennen nur die Zahl der Problemtitel.
    if (stats.state_titles === undefined) return filter === 'problem' ? stats.problem_titles : 0
    return stats.state_titles[kind]?.[filter] ?? 0
  }

  const tone = (filter: StateFilter): string => {
    if (filter === 'problem') return 'bg-bad-500/15 text-bad-500'
    if (filter === 'unclear') return 'bg-accent-500/15 text-accent-400'
    return 'bg-ink-700 text-mist-300'
  }

  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label={t('library.filter.state')}>
      {stateFiltersFor(kind).map((filter) => {
        const selected = value === filter
        const number = count(filter)
        return (
          <button
            key={filter}
            type="button"
            aria-pressed={selected}
            onClick={() => onChange(filter)}
            className={
              'inline-flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors ' +
              (selected ? 'border-accent-500/60 bg-accent-500/15 text-accent-400' : 'border-ink-700 bg-ink-900 text-mist-500 hover:text-mist-100')
            }
          >
            {label(filter)}
            {number > 0 && <span className={'rounded-full px-1.5 text-xs font-semibold tabular-nums ' + tone(filter)}>{formatNumber(number, language)}</span>}
          </button>
        )
      })}
    </div>
  )
}

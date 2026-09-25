import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import type { ProfileLine, ProfileSummary, ScoredFormat } from '../../api/types'
import { FormMessage } from '../../components/ui'
import { formatList, formatNumber } from '../../lib/format'
import { formatGbPerHour, formatScore, languageName, profileLineText, warningText } from './profileText'

/**
 * Ein Profil in Alltagsworten: was genommen, bevorzugt und gemieden wird, die Groessen, wann das
 * Nachbessern aufhoert, und die Hinweise zuerst. Fuer den Ueberblick im Assistenten und fuer
 * die Vorschau beim Import.
 */
export function ProfileSummaryView({ summary, line = null }: { summary: ProfileSummary; line?: ProfileLine | null }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const points = (value: number) => formatNumber(value, language)
  const gb = (value: number | null) => (value === null ? t('profiles.summary.sizeNone') : formatGbPerHour(value, language))

  // Ist das Ziel eine Gruppe gleichwertiger Qualitaeten, steht ihr Inhalt da, nicht ihr innerer Name.
  const cutoffGroup = summary.qualities.find((quality) => typeof quality !== 'string' && quality.group === summary.cutoff)
  const cutoff =
    cutoffGroup !== undefined && typeof cutoffGroup !== 'string'
      ? new Intl.ListFormat(language, { type: 'disjunction' }).format(cutoffGroup.items)
      : summary.cutoff

  const required = summary.languages.filter((entry) => entry.role === 'required').map((entry) => languageName(t, entry.code))
  const preferred = summary.languages.filter((entry) => entry.role === 'preferred').map((entry) => languageName(t, entry.code))
  const requiredText =
    required.length === 0
      ? t('profiles.summary.languagesNone')
      : required.length === 1
        ? t('profiles.summary.languagesOne', { language: required[0] })
        : summary.required_languages === 'any'
          ? t('profiles.summary.languagesAny', { languages: formatList(required, language) })
          : t('profiles.summary.languagesAll', { languages: formatList(required, language) })

  return (
    <div className="flex flex-col gap-5">
      {line && <p className="rounded-xl border border-accent-500/40 bg-accent-500/5 px-4 py-3 font-semibold wrap-anywhere text-mist-100">{profileLineText(t, line, language)}</p>}

      {summary.warnings.length > 0 && (
        <Block title={t('profiles.summary.warningsTitle')}>
          {summary.warnings.map((warning, index) => (
            <FormMessage key={`${warning.code}-${index}`}>{warningText(t, i18n, warning, language)}</FormMessage>
          ))}
        </Block>
      )}

      <Block title={t('profiles.summary.takenTitle')}>
        <p className="text-sm text-mist-400">{t('profiles.summary.taken')}</p>
        <ol className="flex flex-wrap gap-1.5">
          {summary.qualities.map((quality, index) => (
            <li key={index} className="min-w-0 rounded-lg border border-ink-700 bg-ink-900/60 px-2.5 py-1 font-mono text-xs wrap-anywhere text-mist-200">
              {typeof quality === 'string' ? (
                quality
              ) : (
                <>
                  {quality.items.join(', ')} <span className="font-sans text-mist-500">({t('profiles.summary.same')})</span>
                </>
              )}
            </li>
          ))}
        </ol>
      </Block>

      <Block title={t('profiles.summary.languagesTitle')}>
        <p className="text-sm text-mist-300">{requiredText}</p>
        {preferred.length > 0 && <p className="text-sm text-mist-300">{t('profiles.summary.languagesPreferred', { languages: formatList(preferred, language) })}</p>}
      </Block>

      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 sm:grid-cols-[repeat(2,minmax(0,1fr))]">
        <Block title={t('profiles.summary.preferredTitle')}>
          <ScoreList items={summary.preferred} />
        </Block>
        <Block title={t('profiles.summary.avoidedTitle')}>
          <ScoreList items={summary.avoided} />
        </Block>
      </div>

      <Block title={t('profiles.summary.sizeTitle')}>
        <p className="text-sm text-mist-300">
          {summary.max_gb_per_hour !== null
            ? t('profiles.summary.sizeLimit', { value: formatGbPerHour(summary.max_gb_per_hour, language) })
            : t('profiles.summary.sizeNoLimit')}{' '}
          {t('profiles.summary.sizeMinimums')}
        </p>
        {summary.sizes.length > 0 && (
          <div className="overflow-x-auto rounded-xl border border-ink-700">
            <table className="w-full text-sm" aria-label={t('profiles.summary.sizeTable')}>
              <thead className="bg-ink-900 text-left text-xs text-mist-500">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t('profiles.summary.sizeQuality')}
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    {t('profiles.summary.sizeMin')}
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    {t('profiles.summary.sizeMax')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {summary.sizes.map((size) => (
                  <tr key={size.quality} className="border-t border-ink-700/60">
                    <td className="px-3 py-2 font-mono text-xs wrap-anywhere text-mist-200">{size.quality}</td>
                    <td className="px-3 py-2 text-right text-mist-300 tabular-nums">{gb(size.min_gb_per_hour)}</td>
                    <td className="px-3 py-2 text-right text-mist-300 tabular-nums">{gb(size.max_gb_per_hour)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Block>

      <Block title={t('profiles.summary.stopTitle')}>
        <p className="text-sm text-mist-300">
          {summary.min_score > 0 ? t('profiles.summary.minScore', { score: points(summary.min_score) }) : t('profiles.summary.minScoreLow', { score: points(summary.min_score) })}
        </p>
        <p className="text-sm text-mist-300">{t('profiles.summary.cutoff', { cutoff })}</p>
        <p className="text-sm text-mist-300">
          {/* Ohne die Zahl: Bei "best" erreicht praktisch keine Datei TRaSHs Wert, getauscht wird, solange Besseres kommt. */}
          {summary.upgrade_until > summary.min_score ? t('profiles.summary.upgradeUntil') : t('profiles.summary.upgradeAtMinimum')}
        </p>
      </Block>

      <p className="text-xs text-mist-500">{t('profiles.summary.formatsTotal', { count: summary.formats_total, value: points(summary.formats_total) })}</p>
    </div>
  )
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex min-w-0 flex-col gap-2">
      <h3 className="text-sm font-semibold text-mist-300">{title}</h3>
      {children}
    </section>
  )
}

function ScoreList({ items }: { items: ScoredFormat[] }) {
  const { t, i18n } = useTranslation()
  if (items.length === 0) return <p className="text-sm text-mist-500">{t('profiles.summary.nothing')}</p>
  return (
    <ul className="flex flex-col gap-1">
      {items.map((item, index) => (
        <li key={`${item.name}-${index}`} className="flex items-baseline justify-between gap-3 text-sm">
          <span className="min-w-0 wrap-anywhere text-mist-200">{item.name}</span>
          <span className={'shrink-0 tabular-nums ' + (item.score > 0 ? 'text-ok-500' : item.score < 0 ? 'text-bad-500' : 'text-mist-500')}>{formatScore(item.score, i18n.language)}</span>
        </li>
      ))}
    </ul>
  )
}

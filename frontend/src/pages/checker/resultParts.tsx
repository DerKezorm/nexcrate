/**
 * Bausteine eines Bewertungsergebnisses, die der Release-Pruefer und die Suche je Film teilen:
 * die Marke Passt, Vorerst oder Passt nicht, der Satz zu Vorerst, die Gruende, warum etwas nicht
 * passt, die Zeile zur vorhandenen Datei und die Treffer in den Regeln.
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { ReleaseRejection, ReleaseUpgrade, ScoredFormat } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Badge, Button } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { formatScore } from '../profiles/profileText'
import { rejectionText, upgradeText, type FitState } from './checkerText'

/** So viele Treffer stehen zuerst da, der Rest hinter "alle zeigen". */
export const MATCHED_SHOWN = 10

/**
 * Die Marke zum Ergebnis. "Vorerst" ist Bernstein mit Uhr statt gruen: Es passt, wird aber ersetzt.
 * `describedBy` zeigt auf den Satz zu "Vorerst" und gilt nur dort.
 */
export function FitBadge({ state, describedBy }: { state: FitState; describedBy?: string }) {
  const { t } = useTranslation()
  if (state === 'forNow') {
    return (
      <Badge tone="accent" describedBy={describedBy}>
        <Symbol name="clock" className="h-3.5 w-3.5" />
        {t('checker.forNow')}
      </Badge>
    )
  }
  if (state === 'fits') {
    return (
      <Badge tone="ok">
        <Symbol name="check" className="h-3.5 w-3.5" />
        {t('checker.fits')}
      </Badge>
    )
  }
  return (
    <Badge tone="bad">
      <Symbol name="alert" className="h-3.5 w-3.5" />
      {t('checker.fitsNot')}
    </Badge>
  )
}

/** Der Satz zu "Vorerst", mit der Uhr in Bernstein. `text` ueberschreibt ihn, etwa fuer eine Gruppe bei Serien. */
export function ForNowHint({ id, text }: { id?: string; text?: string }) {
  const { t } = useTranslation()
  return (
    <p id={id} className="flex items-start gap-2 text-sm text-mist-200">
      <Symbol name="clock" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
      <span className="min-w-0">{text ?? t('checker.forNowHint')}</span>
    </p>
  )
}

/** Die Gruende in Worten. Ohne Gruende steht nichts da. `toText` ist der Satz je Grund, bei Serien ein anderer. */
export function RejectionList({ rejections, toText }: { rejections: readonly ReleaseRejection[]; toText?: (rejection: ReleaseRejection, language: string) => string }) {
  const { t, i18n } = useTranslation()
  const asText = toText ?? ((rejection: ReleaseRejection, language: string) => rejectionText(t, rejection, language))
  if (rejections.length === 0) return null
  return (
    <div className="flex flex-col gap-1.5">
      <h4 className="text-xs font-semibold text-mist-400">{t('checker.reasonsTitle')}</h4>
      <ul className="flex flex-col gap-1">
        {rejections.map((rejection, index) => (
          <li key={`${rejection.code}-${index}`} className="flex items-start gap-2 text-sm text-bad-500">
            <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="min-w-0 wrap-anywhere">{asText(rejection, i18n.language)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Ob das Release besser waere als die vorhandene Datei. Nur fuer ein Release zeigen, das passt. */
export function UpgradeLine({ upgrade }: { upgrade: ReleaseUpgrade }) {
  const { t, i18n } = useTranslation()
  const text = upgradeText(t, upgrade, i18n.language)
  return (
    <div className={'flex flex-col gap-0.5 rounded-xl border px-3 py-2 text-sm ' + (upgrade.better ? 'border-ok-500/40 bg-ok-500/10' : 'border-ink-700 bg-ink-900/60')}>
      <p className={'flex items-start gap-2 ' + (upgrade.better ? 'text-ok-500' : 'text-mist-200')}>
        <Symbol name={upgrade.better ? 'arrowUp' : 'info'} className="mt-0.5 h-4 w-4 shrink-0" />
        <span className="min-w-0">
          {text.headline}
          {text.reason && <> {text.reason}</>}
        </span>
      </p>
      <p className="pl-6 text-xs wrap-anywhere text-mist-500">{text.current}</p>
    </div>
  )
}

/** Die Regeln, die zutreffen, mit den hoechsten Punkten zuerst. */
export function MatchedRules({ matched }: { matched: readonly ScoredFormat[] }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const [all, setAll] = useState(false)
  const sorted = [...matched].sort((left, right) => right.score - left.score)
  const visible = all ? sorted : sorted.slice(0, MATCHED_SHOWN)

  return (
    <div className="flex flex-col gap-1.5">
      <h4 className="text-xs font-semibold text-mist-400">{t('checker.matchedTitle')}</h4>
      {sorted.length === 0 ? (
        <p className="text-sm text-mist-500">{t('checker.matchedNone')}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {visible.map((format, index) => (
            <li key={`${format.name}-${index}`} className="flex items-baseline justify-between gap-3 text-sm">
              <span className="min-w-0 wrap-anywhere text-mist-200">{format.name}</span>
              <span className={'shrink-0 tabular-nums ' + (format.score > 0 ? 'text-ok-500' : format.score < 0 ? 'text-bad-500' : 'text-mist-500')}>
                {formatScore(format.score, language)}
              </span>
            </li>
          ))}
        </ul>
      )}
      {sorted.length > MATCHED_SHOWN && (
        <div>
          <Button variant="ghost" size="sm" onClick={() => setAll((current) => !current)} aria-expanded={all}>
            {all ? t('checker.showFewer') : t('checker.showAll', { value: formatNumber(sorted.length, language) })}
          </Button>
        </div>
      )}
    </div>
  )
}

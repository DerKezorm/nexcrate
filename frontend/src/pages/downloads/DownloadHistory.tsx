import { useTranslation } from 'react-i18next'

import type { Download } from '../../api/types'
import { Section } from '../../components/ui'
import { formatDateTime } from '../../lib/format'
import { DownloadHead } from './DownloadHead'
import { aftermathText, clientNameOf, failedDetailText, failedReasonText, isKnownProblem, placeText, problemReasonText, scopeCountsText, transferText } from './downloadText'

/**
 * Abgelegte, fehlgeschlagene und entfernte Downloads, der neueste zuerst. Bei einem abgelegten zuerst, wo der Film jetzt
 * liegt, dann der Weg in schlichten Worten; bei einem fehlgeschlagenen der Grund.
 */
export function DownloadHistory({ items }: { items: Download[] }) {
  const { t, i18n } = useTranslation()

  return (
    <Section title={t('downloads.history.title')} intro={t('downloads.history.intro')}>
      {items.length === 0 ? (
        <p className="text-sm text-mist-500">{t('downloads.history.empty')}</p>
      ) : (
        <ol className="flex flex-col">
          {items.map((download) => {
            const at = download.imported_at ?? download.completed_at ?? download.updated_at
            const imported = download.state === 'imported'
            // Ein Serien-Download nennt seine Zahlen statt eines Filmordners (S4).
            const counts = imported ? scopeCountsText(t, download.scope, i18n.language) : null
            const place = imported && counts === null ? placeText(t, download) : null
            const way = imported ? transferText(t, download.transfer) : null
            const client = clientNameOf(t, download)
            const problem = download.problem
            // `failed_reason` sagt, warum. Fehlt er, steht der Satz zum Problem da, wenn es einen gibt.
            const failed =
              download.state !== 'failed'
                ? null
                : (failedReasonText(t, download.failed_reason ?? null, client) ?? (problem !== null && isKnownProblem(problem.code) ? problemReasonText(t, problem.code, client) : null))
            // Seit dem 22.09.2026: was SABnzbd sagte, und was aus dem Fehlschlag wurde.
            const said = download.state === 'failed' ? failedDetailText(t, download.failed_detail, client) : null
            const after = download.state === 'failed' ? aftermathText(t, download.aftermath, i18n.language) : null
            return (
              <li key={download.id} className="flex min-w-0 flex-col gap-1.5 border-t border-ink-700/60 py-3 first:border-t-0 first:pt-0 sm:flex-row sm:gap-4">
                <time dateTime={at} className="shrink-0 text-xs text-mist-500 tabular-nums sm:w-36 sm:pt-1">
                  {formatDateTime(at, i18n.language)}
                </time>
                <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                  <DownloadHead download={download} />
                  {place && <p className="text-sm wrap-anywhere text-mist-200">{place}</p>}
                  {counts && <p className="text-sm text-mist-200">{counts}</p>}
                  {way && <p className="text-sm text-mist-400">{way}</p>}
                  {failed && <p className="text-sm text-mist-300">{failed}</p>}
                  {said && <p className="text-sm text-mist-300">{said}</p>}
                  {after && <p className="text-sm wrap-anywhere text-mist-200">{after}</p>}
                  {/* Bei einem Paket naennte die Zeile nur die zuletzt abgelegte Datei. */}
                  {download.imported_file && (download.scope?.episodes.length ?? 0) <= 1 && (
                    <p className="text-xs wrap-anywhere text-mist-500">
                      {t('downloads.history.file')}: <span className="font-mono text-mist-300">{download.imported_file}</span>
                    </p>
                  )}
                  <p className="font-mono text-xs leading-5 wrap-anywhere text-mist-600">{download.release.title}</p>
                </div>
              </li>
            )
          })}
        </ol>
      )}
    </Section>
  )
}

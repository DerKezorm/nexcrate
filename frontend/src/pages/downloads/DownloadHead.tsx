import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { Download } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Badge } from '../../components/ui'
import { downloadOriginSymbol, downloadOriginText, downloadStateText, scopeText, stateTone } from './downloadText'

/**
 * Der Kopf eines Downloads: Film mit Jahr, Fassung, Zustand und Programm. Lange Namen brechen um,
 * statt die Karte am Telefon zu verbreitern. `children` stehen rechts daneben, etwa ein Knopf.
 * `hint` steht auf Karten unter "Hinweise": Dort ist kein Zustand rosa, und `problem` heisst "Hinweis". Sonst sah ein
 * Hinweis wie etwas aus, das den Besitzer braucht (gefunden in der Sichtpruefung gegen den echten Server, 14.09.2026).
 * Seit Schritt 3c eine kleine Marke, wenn nexcrate den Download von selbst gestartet hat; von Hand geladene haben keine.
 */
export function DownloadHead({ download, hint = false, children }: { download: Download; hint?: boolean; children?: ReactNode }) {
  const { t, i18n } = useTranslation()
  const year = download.title.year !== null && download.title.year > 0 ? download.title.year : null
  // Seit S4: der Umfang eines Serien-Downloads unter dem Titel.
  const scope = scopeText(t, download.scope, i18n.language)
  const tone = stateTone(download.state)
  const stateText = hint && download.state === 'problem' ? t('downloads.state.hint') : downloadStateText(t, download.state, download.step ?? null)
  const origin = downloadOriginText(t, download.origin)

  return (
    <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
      <div className="flex min-w-0 flex-col gap-1.5">
        <h3 className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5 text-base">
          <Link to={`/titel/${download.title.id}`} className="font-semibold wrap-anywhere text-mist-100 hover:text-accent-400">
            {download.title.title}
          </Link>
          {year !== null && <span className="text-sm text-mist-500">{year}</span>}
          {download.title.artist && <span className="text-sm text-mist-300">{download.title.artist}</span>}
          {scope !== null && <span className="text-sm text-mist-300 tabular-nums">{scope}</span>}
        </h3>
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
          <Badge>
            <Symbol name="layers" className="h-3.5 w-3.5" />
            {download.version.label}
          </Badge>
          <Badge tone={hint && tone === 'bad' ? 'info' : tone}>{stateText}</Badge>
          {origin !== null && (
            <Badge>
              <Symbol name={downloadOriginSymbol(download.origin)} className="h-3.5 w-3.5" />
              {origin}
            </Badge>
          )}
          <span className="flex min-w-0 items-center gap-1.5 text-xs text-mist-500">
            <Symbol name="server" className="h-3.5 w-3.5 shrink-0" />
            <span className="min-w-0 wrap-anywhere">{download.client?.name ?? t('downloads.noClient')}</span>
          </span>
        </div>
      </div>
      {children && <div className="flex flex-wrap gap-2">{children}</div>}
    </div>
  )
}

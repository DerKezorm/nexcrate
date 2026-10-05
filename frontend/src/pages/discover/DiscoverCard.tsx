import { useTranslation } from 'react-i18next'

import { PosterImage } from '../../components/PosterImage'
import { Symbol } from '../../components/Symbol'

/**
 * Eine Karte in Entdecken: Poster oder Cover, Titel, eine Zeile darunter. Ein Klick oeffnet das Hinzufuegen, der
 * kleine Knopf oben rechts blendet den Titel fuer immer aus ("Nicht interessiert"). Auf dem Handy steht er immer da,
 * am Rechner erst beim Zeigen auf die Karte oder mit der Tastatur.
 */
export function DiscoverCard({
  title,
  meta,
  extra,
  posterUrl,
  album = false,
  onOpen,
  onHide,
}: {
  title: string
  meta: string
  extra?: string | null
  posterUrl: string | null
  album?: boolean
  onOpen: () => void
  onHide: () => void
}) {
  const { t } = useTranslation()
  return (
    <li className="group relative min-w-0">
      <button
        type="button"
        onClick={onOpen}
        className="flex w-full flex-col gap-2.5 rounded-xl text-left focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent-500"
        aria-label={t('discover.card.open', { title })}
      >
        <div className="w-full transition-transform duration-200 group-hover:-translate-y-1">
          <PosterImage url={posterUrl} placeholder={album ? 'note' : 'film'} square={album} className="shadow-lg shadow-black/30" />
        </div>
        <div className="w-full min-w-0">
          <p className="truncate text-sm font-semibold text-mist-100 group-hover:text-accent-400" title={title}>
            {title}
          </p>
          {meta && <p className="truncate text-xs text-mist-500">{meta}</p>}
          {extra && <p className="truncate text-xs text-mist-500 tabular-nums">{extra}</p>}
        </div>
      </button>
      <button
        type="button"
        onClick={onHide}
        title={t('discover.card.hide')}
        aria-label={t('discover.card.hideLabel', { title })}
        className="absolute top-2 right-2 z-10 inline-flex h-8 w-8 items-center justify-center rounded-full border border-ink-700 bg-ink-950/85 text-mist-300 shadow-lg shadow-black/40 backdrop-blur transition-opacity hover:text-mist-100 focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100"
      >
        <Symbol name="eyeOff" className="h-4 w-4" />
      </button>
    </li>
  )
}

/** Das Raster der Karten, gleich breit wie die Bibliothek. */
export const DISCOVER_GRID = 'grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6'

import { useState } from 'react'

import { posterSrc } from '../api/images'
import { Symbol, type SymbolName } from './Symbol'

/**
 * Poster aus der Quelle, geladen erst in Sichtweite. Fehlt es oder laedt es nicht,
 * bleibt ein ruhiger Platzhalter stehen, ohne erfundenes Bild. Der Titel steht immer
 * daneben, deshalb ist das Bild fuer Vorleseprogramme Schmuck.
 *
 * `placeholder` waehlt das Symbol ohne Bild, `film` ist die Vorgabe fuer Filme und Serien; ein Album (Musik M1) nutzt
 * `note`. `square` ist das Format eines Covers: ein Album ist quadratisch, im Posterformat waere es beschnitten.
 */
export function PosterImage({ url, className = '', placeholder = 'film', square = false }: { url: string | null; className?: string; placeholder?: SymbolName; square?: boolean }) {
  const src = posterSrc(url)
  // Die Adresse, die nicht laden wollte. Kommt eine neue, wird sie wieder versucht.
  const [failed, setFailed] = useState<string | null>(null)
  const show = src !== null && failed !== src

  return (
    <div className={'relative overflow-hidden rounded-xl border border-ink-700 bg-ink-800 ' + (square ? 'aspect-square ' : 'aspect-[2/3] ') + className} aria-hidden="true">
      <Symbol name={placeholder} className="absolute top-1/2 left-1/2 h-1/3 w-1/3 -translate-x-1/2 -translate-y-1/2 text-mist-600" />
      {show && (
        <img
          src={src}
          alt=""
          loading="lazy"
          decoding="async"
          onError={() => setFailed(src)}
          className="absolute inset-0 h-full w-full object-cover"
          data-poster="image"
        />
      )}
    </div>
  )
}

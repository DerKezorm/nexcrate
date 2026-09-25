import { useState } from 'react'

import { posterSrc } from '../../api/images'

/** Bis zu zwei Anfangsbuchstaben eines Namens, fuer den Platzhalter ohne Cover. */
function initialsOf(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '?'
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase()
  return (words[0][0] + words[words.length - 1][0]).toUpperCase()
}

/**
 * Das Cover eines Kuenstlers (Entscheidung 38): das Cover des Albums, das die Karten- und Zeilenkomponenten waehlen,
 * sonst ein Platzhalter mit den Anfangsbuchstaben des Namens statt eines erfundenen Bilds.
 */
export function ArtistCover({ name, url, className = '' }: { name: string; url: string | null; className?: string }) {
  const src = posterSrc(url)
  const [failed, setFailed] = useState<string | null>(null)
  const show = src !== null && failed !== src

  return (
    <div className={'relative aspect-square overflow-hidden rounded-xl border border-ink-700 bg-ink-800 ' + className} aria-hidden="true">
      {!show && <span className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-lg font-bold tracking-wide text-mist-500">{initialsOf(name)}</span>}
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

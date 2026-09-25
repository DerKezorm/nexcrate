import type { AlbumTrack, AlbumUnclearFile } from '../../api/types'

/** Kleinbuchstaben ohne Akzente und Zeichen, fuer den Vergleich von Titelname und Dateiname. */
function plain(text: string): string {
  return text
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

/** Der Vorschlag fuer eine unklare Datei: der erste freie Titel, dessen Name im Dateinamen steht. */
export function proposalFor(file: AlbumUnclearFile, free: AlbumTrack[]): AlbumTrack | null {
  const name = plain(file.relative_path.split('/').pop() ?? '')
  const hits = free.filter((track) => plain(track.name).length > 0 && name.includes(plain(track.name)))
  // Der laengste Name gewinnt: "Intro (Reprise)" schlaegt "Intro".
  hits.sort((a, b) => plain(b.name).length - plain(a.name).length)
  return hits[0] ?? null
}

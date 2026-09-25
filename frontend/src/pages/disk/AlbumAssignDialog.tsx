import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { diskApi } from '../../api/disk'
import { musicApi } from '../../api/music'
import type { AlbumHit, DiskAlbumProposal, DiskFolder } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Symbol } from '../../components/Symbol'
import { Badge, Button, FormMessage, Spinner } from '../../components/ui'
import { albumFromText } from './diskText'

/** Gesucht wird erst, wenn so lange nichts getippt wurde. */
export const ALBUM_SEARCH_DELAY_MS = 400

/** Eine Wahl im Dialog: ein Album der Bibliothek, oder eines von MusicBrainz, das erst hinzugefuegt wird. */
type Choice = { key: string; titleId: number | null; mbid: string | null; title: string; artist: string | null; year: number | null }

function fromProposal(proposal: DiskAlbumProposal): Choice {
  return { key: `p-${proposal.title_id ?? proposal.mbid}`, titleId: proposal.title_id, mbid: proposal.mbid, title: proposal.title, artist: proposal.artist, year: proposal.year }
}

function fromHit(hit: AlbumHit): Choice {
  const year = hit.first_release_date ? Number(hit.first_release_date.slice(0, 4)) || null : null
  return { key: `h-${hit.mbid}`, titleId: hit.in_library, mbid: hit.mbid, title: hit.title, artist: hit.artist, year }
}

function choiceLine(choice: Choice): string {
  const name = choice.year ? `${choice.title} (${choice.year})` : choice.title
  return choice.artist ? `${choice.artist}, ${name}` : name
}

/**
 * "Zuordnen" fuer einen Albumordner (Musik M6, Entscheidungen 18 und 19): die Vorschlaege des Scans, darunter eine Suche
 * bei MusicBrainz. Ein Album, das noch nicht in der Bibliothek steht, fuegt der Dialog zuerst hinzu; danach bekommt es
 * den Ordner, und nexcrate liest ihn ein. Verschoben oder umbenannt wird nichts.
 */
export function AlbumAssignDialog({ folder, onClose, onAssigned }: { folder: DiskFolder; onClose: () => void; onAssigned: (titleId: number) => void }) {
  const { t } = useTranslation()
  const album = folder.album
  const proposals = (album?.proposals ?? []).map(fromProposal)
  // Vorgewaehlt nur ein einziger Vorschlag; bei mehreren waehlt der Besitzer.
  const [chosen, setChosen] = useState<Choice | null>(proposals.length === 1 ? proposals[0] : null)
  // Die Albensuche sucht im Titel; der Kuenstler steht in den Treffern.
  const [query, setQuery] = useState(album?.album ?? '')
  // Mit Vorschlaegen fragt der Dialog MusicBrainz erst, wenn der Besitzer tippt.
  const [touched, setTouched] = useState(proposals.length === 0)
  const [hits, setHits] = useState<Choice[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  useEffect(() => {
    const text = query.trim()
    if (!touched || text.length < 2) {
      setHits(null)
      return
    }
    const abort = new AbortController()
    const timer = window.setTimeout(() => {
      setSearching(true)
      setSearchError(null)
      musicApi.searchAlbums(text, undefined, undefined, abort.signal).then(
        (found) => {
          setHits(found.map(fromHit))
          setSearching(false)
        },
        (error: unknown) => {
          if (abort.signal.aborted) return
          setSearchError(error)
          setSearching(false)
        },
      )
    }, ALBUM_SEARCH_DELAY_MS)
    return () => {
      window.clearTimeout(timer)
      abort.abort()
    }
  }, [query, touched])

  async function submit() {
    if (chosen === null || busy) return
    setBusy(true)
    setProblem(null)
    try {
      let titleId = chosen.titleId
      if (titleId === null && chosen.mbid !== null) titleId = (await musicApi.addAlbum({ mbid: chosen.mbid })).id
      if (titleId === null) return
      await diskApi.assignAlbum(folder.id, titleId)
      onAssigned(titleId)
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  function close() {
    if (!busy) onClose()
  }

  const options = (list: Choice[], label: string) => (
    <ul className="flex flex-col gap-1.5" aria-label={label}>
      {list.map((choice) => (
        <li key={choice.key}>
          <label className="flex min-w-0 cursor-pointer items-start gap-3 rounded-xl border border-ink-700 bg-ink-900/60 p-3 has-[:checked]:border-accent-500">
            <input type="radio" name="album-choice" className="mt-1" checked={chosen?.key === choice.key} onChange={() => setChosen(choice)} disabled={busy} />
            <span className="flex min-w-0 flex-1 flex-col items-start gap-1">
              <span className="text-sm font-medium wrap-anywhere text-mist-100">{choiceLine(choice)}</span>
              {choice.titleId !== null ? <Badge tone="ok">{t('disk.row.inLibrary')}</Badge> : <Badge>{t('disk.album.row.notInLibrary')}</Badge>}
            </span>
          </label>
        </li>
      ))}
    </ul>
  )

  return (
    <Dialog
      open
      title={t('disk.album.assign.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button onClick={() => void submit()} loading={busy} disabled={chosen === null}>
            {t('disk.album.assign.submit')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <p className="text-xs text-mist-500">{t('disk.album.assign.folder')}</p>
          <p className="font-mono text-sm wrap-anywhere text-mist-200">{folder.relative_path}</p>
        </div>
        {proposals.length > 0 && (
          <div className="flex flex-col gap-2">
            <p className="text-sm font-medium text-mist-300">{t('disk.album.assign.proposals')}</p>
            {options(proposals, t('disk.album.assign.proposals'))}
            <p className="text-xs text-mist-500">{albumFromText(t, album?.proposals[0]?.from ?? 'library')}</p>
          </div>
        )}
        <div className="flex flex-col gap-2">
          <label className="flex flex-col gap-1.5 text-sm font-medium text-mist-300">
            {t('disk.album.assign.search')}
            <input
              type="search"
              value={query}
              onChange={(event) => {
                setTouched(true)
                setQuery(event.target.value)
              }}
              disabled={busy}
              className="rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-sm text-mist-100 focus:border-accent-500 focus:outline-none"
            />
          </label>
          <p className="text-xs text-mist-500">{t('disk.album.assign.searchHint')}</p>
          {searching && (
            <p className="flex items-center gap-2 text-sm text-mist-500" role="status">
              <Spinner />
              {t('common.loading')}
            </p>
          )}
          {searchError !== null && <FormMessage>{errorText(t, searchError)}</FormMessage>}
          {hits !== null && !searching && (hits.length > 0 ? options(hits, t('disk.album.assign.search')) : <p className="text-sm text-mist-400">{t('disk.album.assign.searchEmpty')}</p>)}
        </div>
        {chosen !== null && chosen.titleId === null && (
          <p className="flex items-start gap-2 text-sm text-mist-300">
            <Symbol name="plus" className="mt-0.5 h-4 w-4 shrink-0" />
            {t('disk.album.assign.adds')}
          </p>
        )}
        <p className="text-sm text-mist-400">{t('disk.album.assign.reading')}</p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

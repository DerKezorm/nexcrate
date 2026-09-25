import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { downloadsApi } from '../../api/downloads'
import type { AlbumFiles } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { FormMessage, Spinner } from '../../components/ui'
import { viaText } from './tagText'

/** Was aus einer Datei des Downloads wurde, wenn sie keinen Titel bekam. Woertliche Schluessel fuer `keys.test.ts`. */
function decisionText(t: (key: string) => string, decision: string): string {
  switch (decision) {
    case 'loose':
      return t('title.history.mapping.loose')
    case 'not_needed':
      return t('title.history.mapping.notNeeded')
    case 'not_filed':
      return t('title.history.mapping.notFiled')
    case 'other_album':
      return t('title.history.mapping.otherAlbum')
    default:
      return t('title.history.mapping.open')
  }
}

/**
 * Unter "Album abgelegt" im Verlauf (Wunsch des Besitzers, 19.09.2026): auf Klick, welche Datei des Downloads auf
 * welchen Titel kam und wie. Liest `GET /api/downloads/{id}/album-files`, das auch fertige Downloads beantwortet.
 */
export function AlbumFiledMapping({ downloadId }: { downloadId: number }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<AlbumFiles | null>(null)
  const [error, setError] = useState<unknown>(null)

  function toggle() {
    const next = !open
    setOpen(next)
    if (next && data === null) {
      setError(null)
      downloadsApi.albumFiles(downloadId).then(setData, setError)
    }
  }

  const tracks = new Map((data?.tracks ?? []).map((track) => [track.id, track]))
  return (
    <div className="mt-1">
      <button type="button" aria-expanded={open} onClick={toggle} className="inline-flex items-center gap-1 text-xs font-medium text-accent-400 hover:underline">
        <Symbol name={open ? 'chevronDown' : 'chevron'} className="h-3 w-3" />
        {t('title.history.mapping.toggle')}
      </button>
      {open &&
        (error !== null ? (
          <FormMessage>{errorText(t, error)}</FormMessage>
        ) : data === null ? (
          <p className="flex items-center gap-2 text-xs text-mist-500" role="status">
            <Spinner className="h-3 w-3" />
            {t('common.loading')}
          </p>
        ) : (
          <ul className="mt-2 flex flex-col gap-1 text-xs">
            {data.files.map((file) => {
              const track = file.track_id !== null ? tracks.get(file.track_id) : undefined
              const via = viaText(t, file.via)
              return (
                <li key={file.key} className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-baseline gap-2">
                  <span className="font-mono wrap-anywhere text-mist-400">{file.path}</span>
                  <Symbol name="arrow" className="h-3 w-3 text-mist-600" />
                  <span className="text-mist-200">
                    {track !== undefined ? `${track.number ?? track.position} ${track.name}` : decisionText(t, file.decision)}
                    {track !== undefined && file.decision === 'not_needed' && <span className="text-mist-500"> ({decisionText(t, file.decision)})</span>}
                    {track !== undefined && file.decision !== 'not_needed' && via !== null && <span className="text-mist-500"> ({via})</span>}
                  </span>
                </li>
              )
            })}
          </ul>
        ))}
    </div>
  )
}

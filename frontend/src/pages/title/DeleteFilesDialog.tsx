import { useEffect, useState } from 'react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { libraryApi } from '../../api/library'
import { recycleApi } from '../../api/recycle'
import type { DeleteFilesRequest } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, FormMessage } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { formatNumber } from '../../lib/format'

/**
 * Was geloescht werden soll: eine Film-Fassung, eine ganze Serien-Fassung, eine Staffel, eine Folgendatei, ein ganzes
 * Album oder die Datei eines Titels (Gabel 2).
 */
export type DeleteTarget =
  | { kind: 'movie'; versionId: number; label: string }
  | { kind: 'series'; versionId: number; label: string }
  | { kind: 'season'; versionId: number; label: string; season: number; seasonName: string }
  | { kind: 'episode'; versionId: number; label: string; fileId: number; fileName: string }
  | { kind: 'unclear'; versionId: number; label: string; fileId: number; fileName: string }
  | { kind: 'album'; versionId: number; label: string }
  | { kind: 'track'; versionId: number; label: string; fileId: number; trackName: string }

function requestOf(target: DeleteTarget): DeleteFilesRequest {
  if (target.kind === 'season') return { version_id: target.versionId, season: target.season }
  if (target.kind === 'episode' || target.kind === 'unclear') return { version_id: target.versionId, episode_file_id: target.fileId }
  if (target.kind === 'track') return { version_id: target.versionId, track_file_id: target.fileId }
  return { version_id: target.versionId }
}

function textOf(t: TFunction, target: DeleteTarget): string {
  switch (target.kind) {
    case 'season':
      return t('title.deleteFiles.season', { label: target.label, season: target.seasonName })
    case 'episode':
      return t('title.deleteFiles.episode', { label: target.label, file: target.fileName })
    case 'unclear':
      return t('title.deleteFiles.unclear', { label: target.label, file: target.fileName })
    case 'album':
      return t('title.deleteFiles.album')
    case 'track':
      return t('title.deleteFiles.track', { track: target.trackName })
    case 'movie':
      return t('title.deleteFiles.movie', { label: target.label })
    case 'series':
      return t('title.deleteFiles.series', { label: target.label })
  }
}

/**
 * "In den Papierkorb?" (Antwort 2). Sagt, was geht, wie lange es im Papierkorb bleibt und dass die
 * Ueberwachung bleibt. Die Tage kommen vom Server; ohne Antwort fehlt nur dieser Satz.
 */
export function DeleteFilesDialog({ titleId, target, onClose, onDeleted }: { titleId: number; target: DeleteTarget; onClose: () => void; onDeleted: () => void }) {
  const { t, i18n } = useTranslation()
  const notify = useNotice()
  const [days, setDays] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  useEffect(() => {
    let current = true
    recycleApi.get().then(
      (result) => {
        if (current) setDays(result.days)
      },
      () => undefined,
    )
    return () => {
      current = false
    }
  }, [])

  async function moveIntoBin() {
    setBusy(true)
    setProblem(null)
    try {
      const result = await libraryApi.deleteFiles(titleId, requestOf(target))
      notify(result.files > 0 ? t('title.deleteFiles.done', { count: result.files, value: formatNumber(result.files, i18n.language) }) : t('title.deleteFiles.nothing'))
      onDeleted()
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  function close() {
    if (!busy) onClose()
  }

  const text = textOf(t, target)

  return (
    <Dialog
      open
      title={t('title.deleteFiles.title')}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="danger" onClick={() => void moveIntoBin()} loading={busy}>
            {t('title.deleteFiles.confirm')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm wrap-anywhere text-mist-300">{text}</p>
        {days !== null && <p className="text-sm text-mist-400">{t('title.deleteFiles.days', { count: days, value: formatNumber(days, i18n.language) })}</p>}
        {/* Eine Datei ohne Folge laesst keine Folge leer, also gibt es zur Ueberwachung nichts zu sagen. */}
        {target.kind !== 'unclear' && (
          <p className="text-sm text-mist-400">
            {target.kind === 'album' || target.kind === 'track' ? t('title.deleteFiles.watchedAlbum') : t('title.deleteFiles.watched')}
          </p>
        )}
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

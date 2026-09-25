import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../../api/client'
import { musicApi } from '../../api/music'
import type { ArtistSummary } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, FormMessage } from '../../components/ui'

/**
 * Kuenstler entfernen, wie ein Titel (Entscheidung 43): eine Rueckfrage mit der Zahl der Alben, die mit ihm gehen.
 * Dateien bleiben immer liegen, das sagt der Satz gleich mit.
 */
export function RemoveArtistDialog({ artist, onClose, onRemoved }: { artist: ArtistSummary; onClose: () => void; onRemoved: () => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)

  async function remove() {
    setBusy(true)
    setProblem(null)
    try {
      await musicApi.removeArtist(artist.id)
      onRemoved()
    } catch (error) {
      setProblem(error)
      setBusy(false)
    }
  }

  function close() {
    if (!busy) onClose()
  }

  return (
    <Dialog
      open
      title={t('music.artist.remove.title', { name: artist.name })}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="danger" onClick={() => void remove()} loading={busy}>
            {t('music.artist.remove.confirm')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm text-mist-300">{t('music.artist.remove.text', { name: artist.name })}</p>
        {/* Entscheidung 43: die Zahl vorher, Dateien bleiben immer liegen. */}
        <p className="text-sm text-mist-400">
          {artist.albums > 0 ? t('music.artist.remove.albums', { count: artist.albums }) : t('music.artist.remove.noAlbums')}
        </p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  )
}

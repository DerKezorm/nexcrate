import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { downloadsApi } from '../../api/downloads'
import { folderImportApi } from '../../api/folderImport'
import type { Download, FolderFile, FolderPreview, MediaKind } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, Spinner, Toggle } from '../../components/ui'
import { formatNumber } from '../../lib/format'
import { sizeText } from '../../lib/size'
import { FolderPicker } from '../files/FolderPicker'
import { AssignDialog as EpisodesDialog } from './AssignDialog'
import { AssignDialog } from './ForeignJobs'

/** Wie oft und wie lange der Dialog nachsieht, ob die Dateien gelesen sind. Danach wartet der Ordner unter Probleme. */
export const READING_POLL_MS = 1000
const READING_ROUNDS = 30

/** Probleme, bei denen die Wahl der Folgen gleich im Dialog weitergeht. */
const CHOOSING = ['files_unassigned', 'other_series_suspected']

/** Die Videos, zwischen denen die Wahl besteht: Ein Film braucht sie nur bei mehreren und ohne Archive daneben. */
function choosable(files: FolderFile[]): FolderFile[] {
  const videos = files.filter((file) => file.kind === 'video')
  return videos.length > 1 && !files.some((file) => file.kind === 'archive') ? videos : []
}

function largest(files: FolderFile[]): string | null {
  return files.reduce<FolderFile | null>((best, file) => (best === null || file.size_bytes > best.size_bytes ? file : best), null)?.path ?? null
}

/**
 * Import aus einem Ordner (Issue #26), wie der manuelle Import von Radarr und Sonarr: erst den Ordner waehlen, dann
 * zuordnen wie einen fremden Auftrag. Die Dateien bleiben liegen, nexcrate verlinkt oder kopiert sie.
 */
export function FolderImportDialog({ onClose, onDone }: { onClose: () => void; onDone: (message: string) => void }) {
  const { t } = useTranslation()
  const [preview, setPreview] = useState<FolderPreview | null>(null)
  const [file, setFile] = useState<string | null>(null)
  const [byHand, setByHand] = useState(false)
  // Der Download, dessen Folgen von Hand gewaehlt werden: erst liest nexcrate die Dateien, dann waehlt der Besitzer.
  const [reading, setReading] = useState<number | null>(null)
  const [choosing, setChoosing] = useState<Download | null>(null)
  const sentByHand = useRef(false)
  const started = useRef<number | null>(null)

  if (choosing !== null) {
    return (
      <EpisodesDialog
        download={choosing}
        // Abbrechen laesst den Ordner unter Probleme warten, wie vor der Wahl im Dialog.
        onClose={() => onDone(t('downloads.folderImport.byHandDone', { name: choosing.release.title }))}
        onDone={onDone}
      />
    )
  }

  if (reading !== null && preview !== null) {
    return <ReadingDialog downloadId={reading} name={preview.name} onReady={setChoosing} onLater={() => onDone(t('downloads.folderImport.byHandDone', { name: preview.name }))} />
  }

  if (preview === null) {
    return (
      <FolderPicker
        title={t('downloads.folderImport.pickTitle')}
        hint={t('downloads.folderImport.pickHint')}
        start={null}
        onClose={onClose}
        onTake={async (path) => {
          const found = await folderImportApi.preview(path)
          setFile(largest(choosable(found.files)))
          setPreview(found)
        }}
      />
    )
  }

  return (
    <AssignDialog
      job={preview}
      intro={t('downloads.folderImport.intro')}
      seriesHint={t('downloads.folderImport.seriesHint')}
      extra={(kind) => (
        <>
          {kind === 'series' && <Toggle label={t('downloads.folderImport.byHand')} hint={t('downloads.folderImport.byHandHint')} checked={byHand} onChange={setByHand} />}
          <FolderFiles preview={preview} kind={kind} file={file} onFile={setFile} />
        </>
      )}
      submit={(body, kind) => {
        sentByHand.current = kind === 'series' && byHand
        return folderImportApi
          .start({
            ...body,
            path: preview.path,
            ...(kind === 'movie' && file !== null ? { file } : {}),
            ...(sentByHand.current ? { by_hand: true } : {}),
          })
          .then((download) => {
            started.current = download.id
          })
      }}
      onClose={onClose}
      onDone={(message) => {
        // Von Hand geht es gleich weiter zur Wahl der Folgen, sobald nexcrate die Dateien gelesen hat (Issue #26).
        if (sentByHand.current && started.current !== null) setReading(started.current)
        else onDone(message)
      }}
    />
  )
}

/**
 * Wartet, bis nexcrate die Dateien des Ordners gelesen hat und nach den Folgen fragt. Kommt etwas anderes heraus oder
 * dauert es zu lange, wartet der Ordner unter Probleme, wie vor der Wahl im Dialog.
 */
function ReadingDialog({ downloadId, name, onReady, onLater }: { downloadId: number; name: string; onReady: (download: Download) => void; onLater: () => void }) {
  const { t } = useTranslation()
  // Nicht als Abhaengigkeit: der Aufrufer reicht je Zeichnen neue Funktionen herein.
  const ready = useRef(onReady)
  const later = useRef(onLater)
  ready.current = onReady
  later.current = onLater

  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const abort = new AbortController()

    async function round(left: number) {
      const found = await downloadsApi.read(downloadId, abort.signal).catch(() => null)
      if (!active) return
      if (found !== null && found.state === 'problem' && CHOOSING.includes(found.problem?.code ?? '')) ready.current(found)
      else if (found === null || (found.state !== 'completed' && found.state !== 'importing') || left <= 1) later.current()
      else timer = setTimeout(() => void round(left - 1), READING_POLL_MS)
    }

    void round(READING_ROUNDS)
    return () => {
      active = false
      clearTimeout(timer)
      abort.abort()
    }
  }, [downloadId])

  return (
    <Dialog
      open
      title={t('downloads.folderImport.readingTitle', { name })}
      onClose={onLater}
      footer={
        <Button variant="ghost" onClick={onLater}>
          {t('downloads.folderImport.later')}
        </Button>
      }
    >
      <p className="flex items-center gap-2 text-sm text-mist-300" role="status">
        <Spinner />
        {t('downloads.folderImport.reading')}
      </p>
    </Dialog>
  )
}

function FolderFiles({ preview, kind, file, onFile }: { preview: FolderPreview; kind: MediaKind; file: string | null; onFile: (path: string) => void }) {
  const { t, i18n } = useTranslation()
  const choices = kind === 'movie' ? choosable(preview.files) : []
  const heading = t('downloads.folderImport.files')
  return (
    <section className="flex flex-col gap-2" aria-label={heading}>
      <h3 className="text-sm font-semibold text-mist-100">{heading}</h3>
      <p className="font-mono text-xs wrap-anywhere text-mist-400">{preview.path}</p>
      {choices.length > 0 && <p className="text-sm text-mist-300">{t('downloads.folderImport.choose')}</p>}
      <ul className="flex max-h-56 flex-col gap-1 overflow-y-auto rounded-xl border border-ink-700 bg-ink-900/60 p-2">
        {preview.files.map((item) => {
          const size = sizeText(t, item.size_bytes, i18n.language)
          return (
            <li key={item.path} className="flex items-center gap-2 text-sm">
              {choices.includes(item) ? (
                <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2">
                  <input type="radio" name="folder-import-file" aria-label={item.path} className="h-4 w-4 shrink-0 accent-accent-500" checked={file === item.path} onChange={() => onFile(item.path)} />
                  <span className="min-w-0 flex-1 font-mono text-xs wrap-anywhere text-mist-200">{item.path}</span>
                </label>
              ) : (
                <span className="min-w-0 flex-1 font-mono text-xs wrap-anywhere text-mist-200">{item.path}</span>
              )}
              <span className="shrink-0 text-xs text-mist-500 tabular-nums">{size}</span>
            </li>
          )
        })}
      </ul>
      {preview.more_files > 0 && <p className="text-xs text-mist-500">{t('downloads.folderImport.more', { count: preview.more_files, value: formatNumber(preview.more_files, i18n.language) })}</p>}
    </section>
  )
}

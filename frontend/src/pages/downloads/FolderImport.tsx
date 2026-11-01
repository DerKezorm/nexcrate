import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { folderImportApi } from '../../api/folderImport'
import type { FolderFile, FolderPreview, MediaKind } from '../../api/types'
import { formatNumber } from '../../lib/format'
import { sizeText } from '../../lib/size'
import { FolderPicker } from '../files/FolderPicker'
import { AssignDialog } from './ForeignJobs'

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
      extra={(kind) => <FolderFiles preview={preview} kind={kind} file={file} onFile={setFile} />}
      submit={(body, kind) => folderImportApi.start({ ...body, path: preview.path, ...(kind === 'movie' && file !== null ? { file } : {}) })}
      onClose={onClose}
      onDone={onDone}
    />
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

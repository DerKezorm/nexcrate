import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import type { DiskRoot, DiskRootKind } from '../../api/types'
import { Badge, Button, Section } from '../../components/ui'
import { Symbol } from '../../components/Symbol'
import { formatList, formatNumber } from '../../lib/format'
import { whenText } from '../../lib/when'
import { addressOfKind, FILES_TAB_PATH, KIND_PARAM } from '../settings/tabs'
import { shownCounts, stateLabel } from './diskText'

/**
 * The scanned folders: every version's default folder, the folders of owned versions and the folders the owner added,
 * each with its versions, its last scan and the counts of that scan. "Einlesen" per root, "Alle einlesen" and adding a
 * folder through the folder picker; a folder the owner added can leave the list again.
 *
 * Gezeigt werden nur die Wurzeln der gewaehlten Art (S6): unter einer Serienwurzel liegen Serienordner.
 */
export function RootList({
  roots,
  busy,
  onScan,
  onAdd,
  onRemove,
  kind = 'movie',
}: {
  roots: DiskRoot[]
  /** A job runs: nothing new starts until it ends (409 `disk_job_running`). */
  busy: boolean
  onScan: (root: DiskRoot | null) => void
  onAdd: () => void
  onRemove: (root: DiskRoot) => void
  kind?: DiskRootKind
}) {
  const { t } = useTranslation()
  const canScan = roots.some((root) => root.error_code === null)
  const series = kind === 'series'

  return (
    <Section
      title={t('disk.roots.title')}
      intro={kind === 'album' ? t('disk.album.roots.intro') : series ? t('disk.series.roots.intro') : t('disk.roots.intro')}
      actions={
        <>
          <Button variant="ghost" size="sm" onClick={onAdd} disabled={busy}>
            <Symbol name="folder" />
            {t('disk.roots.add')}
          </Button>
          {roots.length > 0 && (
            <Button size="sm" onClick={() => onScan(null)} disabled={busy || !canScan}>
              <Symbol name="refresh" />
              {t('disk.roots.scanAll')}
            </Button>
          )}
        </>
      }
    >
      {roots.length === 0 ? (
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-mist-400">
          <span>{kind === 'album' ? t('disk.album.roots.empty') : series ? t('disk.series.roots.empty') : t('disk.roots.empty')}</span>
          <Link
            to={series || kind === 'album' ? `${FILES_TAB_PATH}&${KIND_PARAM}=${addressOfKind(series ? 'series' : 'music')}` : FILES_TAB_PATH}
            className="font-medium text-accent-400 hover:underline"
          >
            {t('settings.tabs.files')}
          </Link>
        </p>
      ) : (
        <ul aria-label={t('disk.roots.title')} className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
          {roots.map((root) => (
            <li key={root.id} className="min-w-0">
              <RootCard root={root} busy={busy} onScan={() => onScan(root)} onRemove={() => onRemove(root)} />
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

function RootCard({ root, busy, onScan, onRemove }: { root: DiskRoot; busy: boolean; onScan: () => void; onRemove: () => void }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const labels = root.versions.map((version) => version.label)
  const counts = shownCounts(root.counts)
  const invisible = root.error_code !== null
  const missing = typeof root.missing_files === 'number' ? root.missing_files : 0
  const unmapped = Array.isArray(root.radarr_unmapped) ? root.radarr_unmapped.filter((name) => typeof name === 'string' && name !== '') : []

  return (
    <article className="flex h-full min-w-0 flex-col gap-2.5 rounded-xl border border-ink-700 bg-ink-900/60 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="flex min-w-0 items-start gap-2 font-mono text-sm break-all text-mist-100">
          <Symbol name="folder" className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
          <span className="min-w-0">{root.path}</span>
        </p>
        {root.added_by_owner && <Badge tone="accent">{t('disk.roots.ownRoot')}</Badge>}
      </div>
      <p className="text-sm text-mist-300">
        {labels.length > 0 ? t('disk.roots.versions', { count: labels.length, labels: formatList(labels, language) }) : t('disk.roots.noVersion')}
      </p>
      <p className="text-xs text-mist-500">{root.last_scan_at ? t('disk.roots.lastScan', { when: whenText(t, root.last_scan_at, language) }) : t('disk.roots.neverScanned')}</p>
      {counts.length > 0 && (
        <dl aria-label={t('disk.roots.counts')} className="flex flex-wrap gap-1.5">
          {counts.map(([state, value]) => (
            <div key={state} className="inline-flex items-center gap-1.5 rounded-full border border-ink-700 bg-ink-850 px-2.5 py-0.5 text-xs">
              <dt className="text-mist-500">{stateLabel(t, state)}</dt>
              <dd className="font-semibold text-mist-100 tabular-nums">{formatNumber(value, language)}</dd>
            </div>
          ))}
        </dl>
      )}
      {missing > 0 && <p className="text-sm text-mist-300">{t('disk.roots.missingFiles', { count: missing })}</p>}
      {unmapped.length > 0 && (
        <p className="flex items-start gap-2 text-sm text-mist-300">
          <Symbol name="info" className="mt-0.5 h-4 w-4 shrink-0 text-info-500" />
          <span className="min-w-0">{t('disk.roots.radarrUnmapped', { names: formatList(unmapped, language) })}</span>
        </p>
      )}
      {invisible && (
        <p className="flex items-start gap-2 text-sm text-bad-500">
          <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="min-w-0">{t('disk.roots.notVisible')}</span>
        </p>
      )}
      <div className="mt-auto flex flex-wrap gap-2 pt-1">
        <Button size="sm" variant={counts.length === 0 ? 'primary' : 'ghost'} onClick={onScan} disabled={busy || invisible} aria-label={t('disk.roots.scanLabel', { path: root.path })}>
          <Symbol name="refresh" />
          {t('disk.roots.scan')}
        </Button>
        {root.added_by_owner && (
          <Button size="sm" variant="ghost" onClick={onRemove} disabled={busy} aria-label={t('disk.roots.removeLabel', { path: root.path })}>
            {t('disk.roots.remove')}
          </Button>
        )}
      </div>
    </article>
  )
}

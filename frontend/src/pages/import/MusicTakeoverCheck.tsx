import { useTranslation } from 'react-i18next'

import type { TakeoverResult, TakeoverRoot } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, Toggle } from '../../components/ui'
import { formatDateTime, formatNumber } from '../../lib/format'
import { Block, PathCell, TakeoverFigures, WarningNote } from './TakeoverCheck'
import { foundByText, isOptionalRoot, type ChosenFolders, type TakeoverChoices } from './takeoverText'
import { useCounts } from './useCounts'

/** Ein Stammordner aus Lidarr und sein Ordner in nexcrate, gezaehlt in Alben und Dateien. */
function MusicRootRow({ root, pendingLocal, onPick }: { root: TakeoverRoot; pendingLocal: string | null; onPick: () => void }) {
  const { t, i18n } = useTranslation()
  const counts = useCounts()
  const found = foundByText(t, root, i18n.language, 'music')
  const optional = isOptionalRoot(root)
  const unmapped = root.local === null
  const blocking = unmapped && !optional
  const canPick = unmapped || root.found_by === 'chosen' || root.found_by === 'derived'
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-xl border border-ink-700 bg-ink-900/60 p-3">
      <div className="grid grid-cols-[minmax(0,1fr)] items-center gap-2 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
        <PathCell label={t('import.takeover.music.roots.sourceSees')} value={root.remote} empty="" />
        <Symbol name="arrow" className="h-4 w-4 rotate-90 justify-self-center text-mist-500 sm:rotate-0" />
        <PathCell label={t('import.takeover.roots.nexcrateSees')} value={root.local} empty={t('import.takeover.roots.unmapped')} optional={optional} />
      </div>
      <p className="text-xs text-mist-500">{t('import.takeover.music.roots.counts', { albums: counts.albums(root.movies), files: counts.files(root.files) })}</p>
      {found !== null && <p className={'text-sm ' + (blocking ? 'text-bad-500' : 'text-mist-300')}>{found}</p>}
      {pendingLocal !== null && <p className="text-sm wrap-anywhere text-info-500">{t('import.takeover.roots.picked', { local: pendingLocal })}</p>}
      {canPick && (
        <div>
          <Button
            size="sm"
            variant={blocking && pendingLocal === null ? 'primary' : 'ghost'}
            onClick={onPick}
            aria-label={unmapped ? t('import.takeover.roots.chooseLabel', { remote: root.remote }) : t('import.takeover.roots.changeLabel', { remote: root.remote })}
          >
            <Symbol name="folder" />
            {unmapped ? t('import.takeover.roots.choose') : t('import.takeover.roots.change')}
          </Button>
        </div>
      )}
    </li>
  )
}

/**
 * Das Ergebnis der Pruefung einer Lidarr-Verbindung (Musik M6): Ordner mit Zuordnung, Alben und Dateien, was danach
 * gesucht, verbessert und eingelesen wird, Lidarrs laufende Downloads, die Fassung und die Bestaetigungen. Eine Benennung
 * aus Lidarr gibt es nicht: die Dateien bleiben, wo sie liegen (Antwort des Besitzers).
 */
export function MusicTakeoverCheck({
  result,
  choices,
  onChoose,
  chosen,
  sentChosen,
  onPick,
}: {
  result: TakeoverResult
  choices: TakeoverChoices
  onChoose: (change: Partial<TakeoverChoices>) => void
  chosen: ChosenFolders
  sentChosen: ChosenFolders
  onPick: (remote: string) => void
}) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const number = (value: number) => formatNumber(value, language)
  const time = formatDateTime(result.read_at, language)
  const version = result.version
  const blockers = result.blockers
  const examples = Array.isArray(result.missing_examples) ? result.missing_examples : []
  const lines: [string, number | null | undefined][] = [
    ['wanted', result.wanted],
    ['wouldUpgrade', result.would_upgrade],
    ['incomplete', result.incomplete],
    ['unmapped', result.unclear],
  ]
  const lineText = (key: string, count: number): string => {
    const values = { count, value: number(count) }
    switch (key) {
      case 'wanted':
        return t('import.takeover.music.counts.wanted', values)
      case 'wouldUpgrade':
        return t('import.takeover.music.counts.wouldUpgrade', values)
      case 'incomplete':
        return t('import.takeover.music.counts.incomplete', values)
      default:
        return t('import.takeover.music.counts.unmapped', values)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {result.data_from === 'stored' ? (
        <FormMessage tone="info">{t('import.takeover.music.dataStored', { time })}</FormMessage>
      ) : (
        <p className="text-xs text-mist-500">{t('import.takeover.music.dataFresh', { time })}</p>
      )}

      <Block title={t('import.takeover.roots.title')} intro={t('import.takeover.music.roots.intro')}>
        <ul className="flex flex-col gap-2">
          {result.roots.map((root) => {
            const picked = chosen[root.remote]
            const pendingLocal = picked !== undefined && picked !== sentChosen[root.remote] ? picked : null
            return <MusicRootRow key={root.remote} root={root} pendingLocal={pendingLocal} onPick={() => onPick(root.remote)} />
          })}
        </ul>
      </Block>

      <Block title={t('import.takeover.music.counts.title')}>
        <TakeoverFigures
          items={[
            { label: t('import.takeover.music.counts.albums'), value: result.albums ?? 0 },
            { label: t('import.takeover.counts.withFile'), value: result.with_file },
            { label: t('import.takeover.music.counts.files'), value: result.files_found },
            { label: t('import.takeover.counts.missing'), value: result.files_missing, bad: result.files_missing > 0 },
          ]}
        />
        {(result.files_outside ?? 0) > 0 && (
          <p className="text-sm text-mist-300">
            {t('import.takeover.music.counts.outside', { count: result.files_outside ?? 0, value: number(result.files_outside ?? 0) })}
          </p>
        )}
        {result.files_other_size > 0 && (
          <p className="text-xs text-mist-500">{t('import.takeover.music.counts.otherSize', { count: result.files_other_size, value: number(result.files_other_size) })}</p>
        )}
        {examples.length > 0 && (
          <ul
            aria-label={t('import.takeover.counts.missingTitle')}
            className="max-h-48 overflow-y-auto rounded-lg border border-ink-700 bg-ink-950/60 px-3 py-2 font-mono text-xs leading-5 text-mist-300"
          >
            {examples.map((path) => (
              <li key={path} className="break-all">
                {path}
              </li>
            ))}
          </ul>
        )}
        {lines.map(([key, count]) =>
          typeof count === 'number' && count > 0 ? (
            <p key={key} className="text-sm text-mist-300">
              {lineText(key, count)}
            </p>
          ) : null,
        )}
      </Block>

      <Block title={t('import.takeover.music.queue.title')}>
        {result.queue > 0 ? (
          <p className="text-sm text-mist-200">{t('import.takeover.music.queue.active', { count: result.queue, value: number(result.queue) })}</p>
        ) : (
          <p className="text-sm text-mist-400">{t('import.takeover.music.queue.none')}</p>
        )}
      </Block>

      <Block title={t('import.takeover.version.title', { label: version.label })}>
        {!version.has_profile && <p className="text-sm text-mist-200">{t('import.takeover.version.noProfile')}</p>}
        {version.folder !== null ? (
          <div className="flex flex-col gap-0.5">
            <p className="text-sm text-mist-400">
              {t('import.takeover.version.folder')} <span className="font-mono break-all text-mist-200">{version.folder}</span>
            </p>
            <p className="text-xs text-mist-500">{t('import.takeover.music.version.folderHint')}</p>
          </div>
        ) : version.folder_proposal !== null ? (
          <Toggle
            label={t('import.takeover.version.useProposal', { folder: version.folder_proposal })}
            hint={t('import.takeover.music.version.useProposalHint')}
            checked={choices.folder}
            onChange={(on) => onChoose({ folder: on })}
          />
        ) : (
          <p className="text-sm text-mist-200">{t('import.takeover.version.noFolder')}</p>
        )}
      </Block>

      {(blockers.includes('files_missing') || blockers.includes('queue_active')) && (
        <Block title={t('import.takeover.confirm.title')}>
          {blockers.includes('files_missing') && (
            <Toggle
              label={t('import.takeover.confirm.missing')}
              hint={t('import.takeover.music.confirm.missingHint', { count: result.files_missing, value: number(result.files_missing) })}
              checked={choices.missing}
              onChange={(on) => onChoose({ missing: on })}
            />
          )}
          {blockers.includes('queue_active') && (
            <Toggle
              label={t('import.takeover.music.confirm.queue')}
              hint={t('import.takeover.music.confirm.queueHint')}
              checked={choices.queue}
              onChange={(on) => onChoose({ queue: on })}
            />
          )}
        </Block>
      )}

      <Block title={t('import.takeover.keep.title')}>
        <Toggle label={t('import.takeover.keep.label')} hint={t('import.takeover.keep.hint')} checked={choices.keep} onChange={(on) => onChoose({ keep: on })} />
      </Block>

      <WarningNote>{t('import.takeover.music.warning')}</WarningNote>
    </div>
  )
}

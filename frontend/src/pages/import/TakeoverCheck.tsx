import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import type { TakeoverResult, TakeoverRoot } from '../../api/types'
import { Symbol } from '../../components/Symbol'
import { Button, FormMessage, Toggle } from '../../components/ui'
import { formatDateTime, formatNumber } from '../../lib/format'
import { NoteLines } from '../files/PatternFields'
import { SourceNamingView } from '../files/RadarrNamingDialog'
import { SeriesSourceNamingView } from './SonarrNaming'
import { foundByText, isOptionalRoot, isSeriesNaming, isSeriesResult, takeoverNoteTexts, type ChosenFolders, type TakeoverChoices } from './takeoverText'
import { useCounts } from './useCounts'

/** Eine echte Warnung, rosa mit Symbol. Kein `alert`: Sie steht fest da und soll nicht bei jedem Zeichnen angesagt werden. */
export function WarningNote({ children }: { children: ReactNode }) {
  return (
    <div role="note" className="flex items-start gap-2.5 rounded-xl border border-bad-500/40 bg-bad-500/10 px-3.5 py-2.5 text-sm text-mist-100">
      <Symbol name="alert" className="mt-0.5 h-4 w-4 shrink-0 text-bad-500" />
      <p className="min-w-0 wrap-anywhere">{children}</p>
    </div>
  )
}

/** Zahlen mit Beschriftung in Kacheln, zwei nebeneinander am Telefon, vier daneben breiter. */
export function TakeoverFigures({ items }: { items: { label: string; value: number; bad?: boolean }[] }) {
  const { i18n } = useTranslation()
  return (
    <dl className="grid grid-cols-[repeat(2,minmax(0,1fr))] gap-2 sm:grid-cols-[repeat(4,minmax(0,1fr))]">
      {items.map((item) => (
        <div key={item.label} className="flex min-w-0 flex-col gap-0.5 rounded-lg border border-ink-700 bg-ink-900/60 px-3 py-2">
          <dt className="text-xs text-mist-500">{item.label}</dt>
          <dd className={'text-xl font-semibold tabular-nums ' + (item.bad ? 'text-bad-500' : 'text-mist-100')}>{formatNumber(item.value, i18n.language)}</dd>
        </div>
      ))}
    </dl>
  )
}

export function Block({ title, intro, children }: { title: string; intro?: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="flex flex-col gap-2.5">
      <div>
        <h3 className="text-sm font-semibold text-mist-100">{title}</h3>
        {intro && <p className="mt-0.5 text-xs text-mist-500">{intro}</p>}
      </div>
      {children}
    </section>
  )
}

/** Ein Pfad mit Beschriftung. Fehlt er, steht er rot da, ausser die Zuordnung ist freiwillig. */
export function PathCell({ label, value, empty, optional = false }: { label: string; value: string | null; empty: string; optional?: boolean }) {
  const missing = value === null && !optional
  return (
    <div className={'min-w-0 rounded-lg border bg-ink-850 px-3 py-2 ' + (missing ? 'border-bad-500/40' : 'border-ink-700')}>
      <p className="text-xs text-mist-500">{label}</p>
      {value === null ? (
        <p className={'text-sm ' + (missing ? 'text-bad-500' : 'text-mist-400')}>{empty}</p>
      ) : (
        <p className="font-mono text-sm break-all text-mist-100">{value}</p>
      )}
    </div>
  )
}

/**
 * Ein Stammordner aus Radarr und sein Ordner in nexcrate. Ohne Zuordnung, selbst gewaehlt oder abgeleitet, mit der
 * Ordnerauswahl. Ein Ordner ohne Dateien (Befund 13) ist freiwillig: kein Rot, und ein Satz sagt, dass dort nur Filme
 * ohne Datei stehen. Bei Sonarr zaehlt die Zeile Serien statt Filme.
 */
function RootRow({ root, series, pendingLocal, onPick }: { root: TakeoverRoot; series: boolean; pendingLocal: string | null; onPick: () => void }) {
  const { t, i18n } = useTranslation()
  const counts = useCounts()
  const found = foundByText(t, root, i18n.language, series)
  const optional = isOptionalRoot(root)
  const unmapped = root.local === null
  // Nur ein Ordner mit Dateien und ohne Zuordnung sperrt die Uebernahme.
  const blocking = unmapped && !optional
  const canPick = unmapped || root.found_by === 'chosen' || root.found_by === 'derived'

  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-xl border border-ink-700 bg-ink-900/60 p-3">
      <div className="grid grid-cols-[minmax(0,1fr)] items-center gap-2 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
        <PathCell label={series ? t('import.takeover.series.roots.sourceSees') : t('import.takeover.roots.radarrSees')} value={root.remote} empty="" />
        <Symbol name="arrow" className="h-4 w-4 rotate-90 justify-self-center text-mist-500 sm:rotate-0" />
        <PathCell label={t('import.takeover.roots.nexcrateSees')} value={root.local} empty={t('import.takeover.roots.unmapped')} optional={optional} />
      </div>
      <p className="text-xs text-mist-500">{t('import.takeover.roots.counts', { movies: series ? counts.series(root.movies) : counts.movies(root.movies), files: counts.files(root.files) })}</p>
      {optional && <p className="text-sm text-mist-300">{series ? t('import.takeover.series.roots.noFiles') : t('import.takeover.roots.noFiles')}</p>}
      {found !== null && <p className={'text-sm ' + (blocking ? 'text-bad-500' : 'text-mist-300')}>{found}</p>}
      {pendingLocal !== null && (
        <p className="text-sm wrap-anywhere text-info-500">
          {optional ? t('import.takeover.roots.pickedNoFiles', { local: pendingLocal }) : t('import.takeover.roots.picked', { local: pendingLocal })}
        </p>
      )}
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
 * Das Ergebnis einer Pruefung: die Ordner mit ihrer Zuordnung, die Zahlen samt fehlender Dateien, Radarrs laufende
 * Downloads, nexcrates Fassung, Radarrs Benennung und die Bestaetigungen fuer das, was eine Uebernahme sonst ablehnt.
 * Eine Zuordnung waehlt man nur ueber die Ordnerauswahl, nie als Text.
 *
 * Bei einer Verbindung zu Sonarr (S6) heisst alles Serien und Folgendateien, und dazu kommen die Dateien ohne Folge und
 * die Dateien, die nexcrate danach verbessern wuerde.
 */
export function TakeoverCheck({
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
  const counts = useCounts()
  const series = isSeriesResult(result)
  const version = result.version
  const naming = result.naming
  const blockers = result.blockers
  const examples = Array.isArray(result.missing_examples) ? result.missing_examples : []
  const moreMissing = Math.max(0, result.files_missing - examples.length)
  const number = (value: number) => formatNumber(value, language)
  const time = formatDateTime(result.read_at, language)
  const notes = takeoverNoteTexts(t, result.notes, language)
  const unclear = series ? (result.unclear ?? 0) : 0
  const wouldUpgrade = series && typeof result.would_upgrade === 'number' ? result.would_upgrade : null

  return (
    <div className="flex flex-col gap-6">
      {result.data_from === 'stored' ? (
        <FormMessage tone="info">{series ? t('import.takeover.series.dataStored', { time }) : t('import.takeover.dataStored', { time })}</FormMessage>
      ) : (
        <p className="text-xs text-mist-500">{series ? t('import.takeover.series.dataFresh', { time }) : t('import.takeover.dataFresh', { time })}</p>
      )}

      <Block title={t('import.takeover.roots.title')} intro={series ? t('import.takeover.series.roots.intro') : t('import.takeover.roots.intro')}>
        <ul className="flex flex-col gap-2">
          {result.roots.map((root) => {
            const picked = chosen[root.remote]
            const pendingLocal = picked !== undefined && picked !== sentChosen[root.remote] ? picked : null
            return <RootRow key={root.remote} root={root} series={series} pendingLocal={pendingLocal} onPick={() => onPick(root.remote)} />
          })}
        </ul>
      </Block>

      <Block
        title={series ? t('import.takeover.series.counts.title') : t('import.takeover.counts.title')}
        intro={series ? t('import.takeover.series.counts.intro', { series: counts.series(result.series ?? 0), episodeFiles: counts.episodeFiles(result.episode_files ?? 0) }) : undefined}
      >
        <TakeoverFigures
          items={[
            series
              ? { label: t('import.takeover.series.counts.series'), value: result.series ?? 0 }
              : { label: t('import.takeover.counts.movies'), value: result.movies },
            { label: t('import.takeover.counts.withFile'), value: result.with_file },
            { label: t('import.takeover.counts.found'), value: result.files_found },
            { label: t('import.takeover.counts.missing'), value: result.files_missing, bad: result.files_missing > 0 },
          ]}
        />
        {result.files_other_size > 0 && (
          <p className="text-xs text-mist-500">
            {series
              ? t('import.takeover.series.counts.otherSize', { count: result.files_other_size, value: number(result.files_other_size) })
              : t('import.takeover.counts.otherSize', { count: result.files_other_size, value: number(result.files_other_size) })}
          </p>
        )}
        {examples.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <h4 className="text-xs font-semibold text-mist-400">{t('import.takeover.counts.missingTitle')}</h4>
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
            {moreMissing > 0 && <p className="text-xs text-mist-500">{t('import.takeover.counts.missingMore', { count: moreMissing, value: number(moreMissing) })}</p>}
          </div>
        )}
        {unclear > 0 && <p className="text-sm text-mist-300">{t('import.takeover.series.counts.unclear', { count: unclear, value: number(unclear) })}</p>}
        {wouldUpgrade !== null && wouldUpgrade > 0 && (
          <p className="text-sm text-mist-300">{t('import.takeover.series.counts.wouldUpgrade', { count: wouldUpgrade, value: number(wouldUpgrade) })}</p>
        )}
      </Block>

      <Block title={series ? t('import.takeover.series.queue.title') : t('import.takeover.queue.title')}>
        {result.queue > 0 ? (
          <p className="text-sm text-mist-200">
            {series
              ? t('import.takeover.series.queue.active', { count: result.queue, value: number(result.queue) })
              : t('import.takeover.queue.active', { count: result.queue, value: number(result.queue) })}
          </p>
        ) : (
          <p className="text-sm text-mist-400">{series ? t('import.takeover.series.queue.none') : t('import.takeover.queue.none')}</p>
        )}
      </Block>

      <Block title={t('import.takeover.version.title', { label: version.label })}>
        {!version.has_profile && <p className="text-sm text-mist-200">{t('import.takeover.version.noProfile')}</p>}
        {version.folder !== null ? (
          <div className="flex flex-col gap-0.5">
            <p className="text-sm text-mist-400">
              {t('import.takeover.version.folder')} <span className="font-mono break-all text-mist-200">{version.folder}</span>
            </p>
            <p className="text-xs text-mist-500">{series ? t('import.takeover.series.version.folderHint') : t('settings.files.folders.hint')}</p>
          </div>
        ) : version.folder_proposal !== null ? (
          <Toggle
            label={t('import.takeover.version.useProposal', { folder: version.folder_proposal })}
            hint={series ? t('import.takeover.series.version.useProposalHint') : t('import.takeover.version.useProposalHint')}
            checked={choices.folder}
            onChange={(on) => onChoose({ folder: on })}
          />
        ) : (
          <p className="text-sm text-mist-200">{t('import.takeover.version.noFolder')}</p>
        )}
        {version.has_profile && version.folder !== null && <p className="text-sm text-mist-400">{t('import.takeover.version.ready')}</p>}
      </Block>

      <Block title={t('import.takeover.naming.title')}>
        {naming === null ? (
          <p className="text-sm text-mist-400">{series ? t('import.takeover.series.naming.unavailable') : t('import.takeover.naming.unavailable')}</p>
        ) : (
          <>
            {isSeriesNaming(naming) ? <SeriesSourceNamingView naming={naming} /> : <SourceNamingView naming={naming} />}
            {naming.can_take ? (
              <Toggle
                label={series ? t('import.takeover.series.naming.take', { label: version.label }) : t('import.takeover.naming.take', { label: version.label })}
                hint={series ? t('import.takeover.series.naming.takeHint') : t('import.takeover.naming.takeHint')}
                checked={choices.naming}
                onChange={(on) => onChoose({ naming: on })}
              />
            ) : (
              <p className="text-sm text-mist-300">
                {series ? t('import.takeover.series.naming.cannotTake', { label: version.label }) : t('import.takeover.naming.cannotTake', { label: version.label })}
              </p>
            )}
          </>
        )}
      </Block>

      {notes.length > 0 && (
        <Block title={series ? t('import.takeover.series.notes.title') : t('import.takeover.notes.title')}>
          <NoteLines notes={notes} />
        </Block>
      )}

      {(blockers.includes('files_missing') || blockers.includes('queue_active')) && (
        <Block title={t('import.takeover.confirm.title')}>
          {blockers.includes('files_missing') && (
            <Toggle
              label={t('import.takeover.confirm.missing')}
              hint={
                series
                  ? t('import.takeover.series.confirm.missingHint', { count: result.files_missing, value: number(result.files_missing) })
                  : t('import.takeover.confirm.missingHint', { count: result.files_missing, value: number(result.files_missing) })
              }
              checked={choices.missing}
              onChange={(on) => onChoose({ missing: on })}
            />
          )}
          {blockers.includes('queue_active') && (
            <Toggle
              label={series ? t('import.takeover.series.confirm.queue') : t('import.takeover.confirm.queue')}
              hint={series ? t('import.takeover.series.confirm.queueHint') : t('import.takeover.confirm.queueHint')}
              checked={choices.queue}
              onChange={(on) => onChoose({ queue: on })}
            />
          )}
        </Block>
      )}

      <Block title={t('import.takeover.keep.title')}>
        <Toggle label={t('import.takeover.keep.label')} hint={t('import.takeover.keep.hint')} checked={choices.keep} onChange={(on) => onChoose({ keep: on })} />
      </Block>

      <WarningNote>{series ? t('import.takeover.series.warning') : t('import.takeover.warning')}</WarningNote>
    </div>
  )
}

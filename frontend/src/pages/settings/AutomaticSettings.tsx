import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { automaticApi, automaticMusicSwitch, automaticSeriesSwitch } from '../../api/automatic'
import { errorText } from '../../api/client'
import type { Indexer } from '../../api/types'
import { Symbol, type SymbolName } from '../../components/Symbol'
import { Badge, FormMessage, Section, Switch } from '../../components/ui'
import { useNotice } from '../../components/useNotice'
import { Loading } from '../files/PatternFields'
import { lastErrorText, momentText, pausedUntil } from '../indexers/indexerText'
import { useIndexers } from '../indexers/useIndexers'
import { automaticPauseText, limitSourceText, rssGapText, rssLastText, usageTexts, usedUpTexts } from './automaticText'
import { Tile, TileHeader } from './parts'
import { UpgradeBrakes } from './UpgradeBrakes'
import { INDEXERS_TAB_PATH } from './tabs'
import { useSwitchSetting } from './useSwitchSetting'

/**
 * Reiter "Automatik" (Schritt 3c, S5, Musik M5): oben je ein Schalter fuer Filme, Serien und Musik, was nexcrate von selbst sucht und
 * laedt, beide ab Werk aus.
 * Darunter je Indexer, wann nexcrate dort zuletzt neue Releases gelesen hat, was der Indexer an Anfragen meldet und ob die
 * Automatik dort Pause macht. Das Tageslimit selbst traegt man im Dialog des Indexers ein.
 */
export function AutomaticSettings() {
  const { t } = useTranslation()
  const notify = useNotice()
  const setting = useSwitchSetting(automaticApi)
  const series = useSwitchSetting(automaticSeriesSwitch)
  const music = useSwitchSetting(automaticMusicSwitch)
  const { indexers, error } = useIndexers()

  async function change(next: boolean) {
    const saved = await setting.change(next)
    if (saved !== null) notify(saved ? t('settings.automatic.savedOn') : t('settings.automatic.savedOff'))
  }

  async function changeSeries(next: boolean) {
    const saved = await series.change(next)
    if (saved !== null) notify(saved ? t('settings.automatic.seriesSavedOn') : t('settings.automatic.seriesSavedOff'))
  }

  async function changeMusic(next: boolean) {
    const saved = await music.change(next)
    if (saved !== null) notify(saved ? t('settings.automatic.musicSavedOn') : t('settings.automatic.musicSavedOff'))
  }

  return (
    <div className="flex flex-col gap-4">
      <Section title={t('settings.automatic.title')}>
        {setting.enabled === null ? (
          setting.loadError !== null ? (
            <FormMessage>{errorText(t, setting.loadError)}</FormMessage>
          ) : (
            <Loading />
          )
        ) : (
          <div className="flex flex-col gap-3">
            <Switch
              label={t('settings.automatic.switch')}
              hint={t('settings.automatic.hint')}
              checked={setting.enabled}
              onChange={(next) => void change(next)}
              disabled={setting.busy}
            />
            {setting.problem !== null && <FormMessage>{errorText(t, setting.problem)}</FormMessage>}
            {series.enabled !== null && (
              <Switch
                label={t('settings.automatic.seriesSwitch')}
                hint={t('settings.automatic.seriesHint')}
                checked={series.enabled}
                onChange={(next) => void changeSeries(next)}
                disabled={series.busy}
              />
            )}
            {series.problem !== null && <FormMessage>{errorText(t, series.problem)}</FormMessage>}
            {music.enabled !== null && (
              <Switch
                label={t('settings.automatic.musicSwitch')}
                hint={t('settings.automatic.musicHint')}
                checked={music.enabled}
                onChange={(next) => void changeMusic(next)}
                disabled={music.busy}
              />
            )}
            {music.problem !== null && <FormMessage>{errorText(t, music.problem)}</FormMessage>}
          </div>
        )}
      </Section>

      <UpgradeBrakes />

      <Section title={t('settings.automatic.indexers.title')} intro={t('settings.automatic.indexers.intro')}>
        {error !== null && <FormMessage>{errorText(t, error)}</FormMessage>}
        {indexers === null ? (
          error === null && <Loading />
        ) : indexers.length === 0 ? (
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-mist-500">
            <span>{t('settings.automatic.indexers.empty')}</span>
            <Link to={INDEXERS_TAB_PATH} className="font-medium text-accent-400 hover:underline">
              {t('settings.automatic.indexers.link')}
            </Link>
          </p>
        ) : (
          <>
            <ul className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
              {indexers.map((indexer) => (
                <li key={indexer.id} className="min-w-0">
                  <IndexerState indexer={indexer} />
                </li>
              ))}
            </ul>
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-mist-500">
              <span>{t('settings.automatic.indexers.limitNote')}</span>
              <Link to={INDEXERS_TAB_PATH} className="font-medium text-accent-400 hover:underline">
                {t('settings.automatic.indexers.link')}
              </Link>
            </p>
          </>
        )}
      </Section>
    </div>
  )
}

/** Eine Zeile mit Symbol davor. Lange Namen und Zeiten brechen um, statt die Karte zu verbreitern. */
function Line({ symbol, tone, children }: { symbol: SymbolName; tone?: 'info' | 'bad'; children: ReactNode }) {
  const color = tone === 'info' ? 'text-info-500' : tone === 'bad' ? 'text-bad-500' : 'text-mist-500'
  return (
    <p className="flex items-start gap-2 text-sm text-mist-300">
      <Symbol name={symbol} className={'mt-0.5 h-4 w-4 shrink-0 ' + color} />
      <span className="min-w-0 wrap-anywhere">{children}</span>
    </p>
  )
}

/** Was die Automatik ueber einen Indexer weiss: neue Releases, Anfragen, Limit und Pausen. */
function IndexerState({ indexer }: { indexer: Indexer }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const paused = pausedUntil(indexer)
  const gap = rssGapText(t, indexer, language)
  const usage = usageTexts(t, indexer.usage, language)
  const usedUp = usedUpTexts(t, indexer.usage, language)
  const limit = limitSourceText(t, indexer, language)
  const automaticPause = automaticPauseText(t, indexer, language)

  return (
    <Tile className="h-full">
      <TileHeader title={indexer.name}>
        {!indexer.enabled && (
          <Badge>
            <Symbol name="eyeOff" className="h-3.5 w-3.5" />
            {t('indexers.state.disabled')}
          </Badge>
        )}
        {paused !== null && (
          <Badge tone="info">
            <Symbol name="clock" className="h-3.5 w-3.5" />
            {t('indexers.state.paused', { time: momentText(paused, language) })}
          </Badge>
        )}
      </TileHeader>
      <Line symbol="refresh">{rssLastText(t, indexer, language)}</Line>
      {gap !== null && (
        <Line symbol="info" tone="info">
          {gap}
        </Line>
      )}
      {usage.length > 0 && (
        <p className="flex items-start gap-2 text-sm text-mist-300">
          <Symbol name="pulse" className="mt-0.5 h-4 w-4 shrink-0 text-mist-500" />
          <span className="flex min-w-0 flex-wrap gap-x-3 gap-y-0.5 tabular-nums">
            {usage.map((text) => (
              <span key={text}>{text}</span>
            ))}
          </span>
        </p>
      )}
      {usedUp.map((text) => (
        <Line key={text} symbol="clock" tone="info">
          {text}
        </Line>
      ))}
      {limit !== null && <Line symbol="shield">{limit}</Line>}
      {automaticPause !== null && (
        <div className="flex flex-col gap-1">
          <Line symbol="clock" tone="info">
            {automaticPause}
          </Line>
          {indexer.last_error_code !== null && (
            <p className="pl-6 text-sm wrap-anywhere text-mist-400">{t('indexers.lastError.label', { text: lastErrorText(t, indexer.last_error_code) })}</p>
          )}
        </div>
      )}
    </Tile>
  )
}

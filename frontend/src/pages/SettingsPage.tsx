import { useTranslation } from 'react-i18next'
import { Navigate, useSearchParams } from 'react-router-dom'

import { TabRow, type Tab } from '../components/TabRow'
import { PageTitle } from '../components/ui'
import { AccountSettings } from './settings/AccountSettings'
import { ApiKeySettings } from './settings/ApiKeySettings'
import { AutomaticSettings } from './settings/AutomaticSettings'
import { BackupSettings } from './settings/BackupSettings'
import { ClientSettings } from './settings/ClientSettings'
import { FileSettings } from './settings/FileSettings'
import { ImportSettings } from './settings/ImportSettings'
import { IndexerSettings } from './settings/IndexerSettings'
import { LidarrImportSettings } from './settings/LidarrImportSettings'
import { LogSettings } from './settings/LogSettings'
import { MusicVersionSettings } from './settings/MusicVersionSettings'
import { NotificationSettings } from './settings/NotificationSettings'
import { QualitySettings } from './settings/QualitySettings'
import { ServerSettings } from './settings/ServerSettings'
import {
  addressOfFileTopic,
  addressOfKind,
  addressOfQualityTopic,
  addressOfTab,
  FILE_TOPICS,
  fileTopicFromAddress,
  IMPORT_APPS,
  importAppFromAddress,
  KIND_PARAM,
  kindFromAddress,
  QUALITY_TOPICS,
  qualityTopicFromAddress,
  SYSTEM_TABS,
  tabFromAddress,
  VERSION_KINDS,
  type FileTopic,
  type ImportApp,
  type QualityTopic,
  type SettingsTab,
} from './settings/tabs'
import { ServicesSettings } from './settings/ServicesSettings'
import { KIND_SYMBOL, useKindLabel, type KindSection } from './settings/useKindLabel'
import { VersionSettings } from './settings/VersionSettings'
import { TagsTab } from './settings/TagsTab'
import { WebhookSettings } from './settings/WebhookSettings'

/**
 * Einstellungen im Aufbau von nexbeat: Reiter oben, eine zweite Reihe bei "System", "Fassungen" (je Medienart),
 * "Ordner und Benennung" (je Thema) und "Import" (je App). Reiter und Unterreiter stehen in der Adresse (`?reiter=import&unter=sonarr`), damit Verweise von
 * anderen Seiten genau dort landen; der erste Unterreiter steht nicht darin.
 */
export function SettingsPage() {
  const { t } = useTranslation()
  const [params, setParams] = useSearchParams()
  const kindLabel = useKindLabel()
  const tab = tabFromAddress(params.get('reiter'))
  const kind = kindFromAddress(params.get('unter'))
  const app = importAppFromAddress(params.get('unter'))
  const fileTopic = fileTopicFromAddress(params.get('unter'), params.get('reiter'))
  const qualityTopic = qualityTopicFromAddress(params.get('unter'))

  function change(next: SettingsTab) {
    setParams({ reiter: addressOfTab(next) }, { replace: true })
  }

  function changeSub(value: string) {
    // Unter "Ordner und Benennung" und unter "Qualitaet" bleibt die gewaehlte Medienart, wenn man das Thema wechselt.
    const kindAddress = tab === 'files' || tab === 'quality' ? params.get(KIND_PARAM) : null
    setParams({ reiter: addressOfTab(tab), unter: value, ...(kindAddress === null ? {} : { [KIND_PARAM]: kindAddress }) }, { replace: true })
  }

  const inSystem = SYSTEM_TABS.includes(tab)
  const topTabs: Tab<SettingsTab>[] = [
    { value: 'versions', label: t('settings.tabs.versions'), symbol: 'layers' },
    // Die Qualitaeten und die Formate gehoeren zu den Profilen und stehen deshalb neben den Fassungen.
    { value: 'quality', label: t('settings.tabs.quality'), symbol: 'shield' },
    { value: 'services', label: t('settings.tabs.services'), symbol: 'globe' },
    { value: 'indexers', label: t('settings.tabs.indexers'), symbol: 'search' },
    { value: 'clients', label: t('settings.tabs.clients'), symbol: 'download' },
    { value: 'files', label: t('settings.tabs.files'), symbol: 'folder' },
    // Die Automatik braucht Indexer, Download-Programme und Ordner, deshalb steht sie dahinter.
    { value: 'automatic', label: t('settings.tabs.automatic'), symbol: 'clock' },
    { value: 'servers', label: t('settings.tabs.servers'), symbol: 'server' },
    { value: 'notifications', label: t('settings.tabs.notifications'), symbol: 'bell' },
    { value: 'tags', label: t('settings.tabs.tags'), symbol: 'tag' },
    // Der Import ist ein Hilfsmittel, nicht der Hauptweg, deshalb hinten vor System.
    { value: 'import', label: t('settings.tabs.import'), symbol: 'import' },
    { value: 'logs', label: t('settings.tabs.system'), symbol: 'pulse' },
  ]
  const systemTabs: Tab<SettingsTab>[] = [
    { value: 'logs', label: t('settings.tabs.logs') },
    { value: 'account', label: t('settings.tabs.account') },
    // Sicherungen gehoeren zum Betrieb der Installation, wie das Konto.
    { value: 'backups', label: t('settings.tabs.backups') },
    // Schluessel fuer andere Programme gehoeren zum Zugang, deshalb neben dem Konto.
    { value: 'apikeys', label: t('settings.tabs.apikeys') },
    { value: 'webhooks', label: t('settings.tabs.webhooks') },
  ]
  const kindTabs: Tab<KindSection>[] = VERSION_KINDS.map((value) => ({ value, label: kindLabel(value), symbol: KIND_SYMBOL[value] }))
  // Jede App gehoert zu einer Medienart und traegt deren Zeichen.
  const appLabels: Record<ImportApp, string> = { radarr: t('settings.tabs.radarr'), sonarr: t('settings.tabs.sonarr'), lidarr: t('settings.tabs.lidarr') }
  const fileLabels: Record<FileTopic, string> = {
    folders: t('settings.files.topics.folders'),
    naming: t('settings.files.topics.naming'),
    rename: t('settings.files.topics.rename'),
    subtitles: t('settings.files.topics.subtitles'),
    companions: t('settings.files.topics.companions'),
    recycle: t('settings.files.topics.recycle'),
  }
  const fileTabs: Tab<FileTopic>[] = FILE_TOPICS.map((value) => ({ value, label: fileLabels[value] }))
  const qualityLabels: Record<QualityTopic, string> = {
    profiles: t('quality.topics.profiles'),
    sizes: t('quality.topics.sizes'),
    formats: t('quality.topics.formats'),
  }
  const qualityTabs: Tab<QualityTopic>[] = QUALITY_TOPICS.map((value) => ({ value, label: qualityLabels[value] }))
  const appTabs: Tab<ImportApp>[] = IMPORT_APPS.map((value, index) => ({ value, label: appLabels[value], symbol: KIND_SYMBOL[VERSION_KINDS[index]] }))

  return (
    <div className="flex flex-col gap-6">
      <PageTitle sub={t('settings.sub')}>{t('settings.title')}</PageTitle>
      <TabRow label={t('settings.tabs.label')} tabs={topTabs} active={inSystem ? 'logs' : tab} onChange={change} />
      {inSystem && <TabRow sub label={t('settings.tabs.system')} tabs={systemTabs} active={tab} onChange={change} />}
      {tab === 'versions' && <TabRow sub label={t('settings.tabs.versions')} tabs={kindTabs} active={kind} onChange={(next) => changeSub(addressOfKind(next))} />}
      {tab === 'files' && <TabRow sub label={t('settings.tabs.files')} tabs={fileTabs} active={fileTopic} onChange={(next) => changeSub(addressOfFileTopic(next))} />}
      {tab === 'quality' && <TabRow sub label={t('settings.tabs.quality')} tabs={qualityTabs} active={qualityTopic} onChange={(next) => changeSub(addressOfQualityTopic(next))} />}
      {tab === 'import' && <TabRow sub label={t('settings.tabs.import')} tabs={appTabs} active={app} onChange={changeSub} />}

      {tab === 'versions' && kind === 'movie' && <VersionSettings kind="movie" />}
      {tab === 'versions' && kind === 'series' && <VersionSettings kind="series" />}
      {tab === 'versions' && kind === 'music' && <MusicVersionSettings />}
      {tab === 'quality' && <QualitySettings />}
      {tab === 'services' && <ServicesSettings />}
      {tab === 'indexers' && <IndexerSettings />}
      {tab === 'clients' && <ClientSettings />}
      {tab === 'files' && <FileSettings />}
      {tab === 'automatic' && <AutomaticSettings />}
      {tab === 'import' && app === 'radarr' && <ImportSettings app="radarr" />}
      {tab === 'import' && app === 'sonarr' && <ImportSettings app="sonarr" />}
      {tab === 'import' && app === 'lidarr' && <LidarrImportSettings />}
      {tab === 'servers' && <ServerSettings />}
      {tab === 'notifications' && <NotificationSettings />}
      {tab === 'tags' && <TagsTab />}
      {tab === 'logs' && <LogSettings />}
      {tab === 'account' && <AccountSettings />}
      {tab === 'backups' && <BackupSettings />}
      {tab === 'apikeys' && <ApiKeySettings />}
      {tab === 'webhooks' && <WebhookSettings />}
      {/* "Ueber" steht seit der Fusszeile auf einer eigenen Seite; alte Verweise landen dort. */}
      {tab === 'about' && <Navigate to="/ueber" replace />}
    </div>
  )
}

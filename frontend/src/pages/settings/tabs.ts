import type { KindSection } from './useKindLabel'

export type SettingsTab = 'versions' | 'quality' | 'services' | 'indexers' | 'clients' | 'files' | 'automatic' | 'servers' | 'notifications' | 'tags' | 'import' | 'logs' | 'account' | 'backups' | 'apikeys' | 'webhooks' | 'about'

/** Der Reiter "Import". Die leere Bibliothek und die alte Adresse `/uebernahme` fuehren hierher. */
export const IMPORT_TAB_PATH = '/einstellungen?reiter=import'

/** Unterreiter "Sonarr" unter "Import", etwa aus der leeren Bibliothek der Serien. */
export const SONARR_IMPORT_TAB_PATH = '/einstellungen?reiter=import&unter=sonarr'

/** Der Reiter "Fassungen", etwa aus dem Dialog "Hinzufuegen", solange es noch keine gibt. */
export const VERSIONS_TAB_PATH = '/einstellungen?reiter=fassungen'

/** Unterreiter "Serien" unter "Fassungen", etwa aus dem Dialog "Hinzufuegen" fuer Serien. */
export const SERIES_VERSIONS_TAB_PATH = '/einstellungen?reiter=fassungen&unter=serien'

/** Seit M4: die Musik-Fassung, fuer den Weg aus den Ordnern. */
export const MUSIC_VERSIONS_TAB_PATH = '/einstellungen?reiter=fassungen&unter=musik'

/** Der Reiter "Qualitaet", etwa aus dem Profil von Hand, wenn eine Groesse oder ein Format fehlt. */
export const QUALITY_TAB_PATH = '/einstellungen?reiter=qualitaet'

/** Unterreiter "Custom Formats" unter "Qualitaet". */
export const FORMATS_TAB_PATH = '/einstellungen?reiter=qualitaet&unter=formate'

/** Der Reiter "Indexer", etwa von der Titelseite, wenn kein Indexer eingeschaltet ist. */
export const INDEXERS_TAB_PATH = '/einstellungen?reiter=indexer'

/** Der Reiter "Download-Programme", etwa von der Suche, wenn fuer ein Protokoll keines eingeschaltet ist. */
export const CLIENTS_TAB_PATH = '/einstellungen?reiter=downloads'

/** Der Reiter "Ordner und Benennung", etwa von der Suche, wenn eine Fassung noch keinen Ordner hat. */
export const FILES_TAB_PATH = '/einstellungen?reiter=ordner'

/** Der Reiter "Automatik" (Schritt 3c), etwa von der Titelseite, solange der Schalter aus ist. */
export const AUTOMATIC_TAB_PATH = '/einstellungen?reiter=automatik'

/** Der Reiter "Online-Dienste" mit dem TMDB-Token, etwa von der Ordnerseite, solange keiner gespeichert ist. */
export const TMDB_TAB_PATH = '/einstellungen?reiter=dienste'

/** Was unter "System" liegt und deshalb eine zweite Reiterreihe bekommt. */
export const SYSTEM_TABS: readonly SettingsTab[] = ['logs', 'account', 'backups', 'apikeys', 'webhooks']

/**
 * ⚠️ Deutsche Woerter in der Adresse, englische Werte im Code, wie in nexbeat.
 * Die Namen sind eine Zusage: Verweise wie `?reiter=import` stehen in anderen Seiten,
 * etwa in der leeren Bibliothek und hinter der alten Adresse `/uebernahme`.
 */
const FROM_ADDRESS: Record<string, SettingsTab> = {
  fassungen: 'versions',
  qualitaet: 'quality',
  dienste: 'services',
  // Frueher hiess der Reiter "TMDB"; alte Verweise landen bei den Online-Diensten.
  tmdb: 'services',
  import: 'import',
  downloads: 'clients',
  indexer: 'indexers',
  ordner: 'files',
  benennung: 'files',
  automatik: 'automatic',
  medienserver: 'servers',
  benachrichtigungen: 'notifications',
  tags: 'tags',
  system: 'logs',
  protokoll: 'logs',
  konto: 'account',
  sicherungen: 'backups',
  schluessel: 'apikeys',
  webhooks: 'webhooks',
  ueber: 'about',
}

const TO_ADDRESS: Record<SettingsTab, string> = {
  versions: 'fassungen',
  quality: 'qualitaet',
  services: 'dienste',
  import: 'import',
  clients: 'downloads',
  indexers: 'indexer',
  files: 'ordner',
  automatic: 'automatik',
  servers: 'medienserver',
  notifications: 'benachrichtigungen',
  tags: 'tags',
  logs: 'protokoll',
  account: 'konto',
  backups: 'sicherungen',
  apikeys: 'schluessel',
  webhooks: 'webhooks',
  about: 'ueber',
}

export function tabFromAddress(value: string | null): SettingsTab {
  return value !== null && Object.hasOwn(FROM_ADDRESS, value) ? FROM_ADDRESS[value] : 'versions'
}

export function addressOfTab(tab: SettingsTab): string {
  return TO_ADDRESS[tab]
}

/** Unterreiter unter "Import": je App ein eigener. Radarr ist der erste und steht deshalb nicht in der Adresse. */
export type ImportApp = 'radarr' | 'sonarr' | 'lidarr'

export const IMPORT_APPS: readonly ImportApp[] = ['radarr', 'sonarr', 'lidarr']

export function importAppFromAddress(value: string | null): ImportApp {
  return IMPORT_APPS.find((app) => app === value) ?? 'radarr'
}

/** Unterreiter unter "Fassungen": je Medienart einer. Filme sind der erste und stehen nicht in der Adresse. */
export const VERSION_KINDS: readonly KindSection[] = ['movie', 'series', 'music']

const KIND_TO_ADDRESS: Record<KindSection, string> = { movie: 'filme', series: 'serien', music: 'musik' }

export function kindFromAddress(value: string | null): KindSection {
  return VERSION_KINDS.find((kind) => KIND_TO_ADDRESS[kind] === value) ?? 'movie'
}

export function addressOfKind(kind: KindSection): string {
  return KIND_TO_ADDRESS[kind]
}

/** Unterreiter unter "Qualitaet": die Profile, die Groessen und die Custom Formats. Die Profile sind der erste. */
export type QualityTopic = 'profiles' | 'sizes' | 'formats'

export const QUALITY_TOPICS: readonly QualityTopic[] = ['profiles', 'sizes', 'formats']

const QUALITY_TOPIC_TO_ADDRESS: Record<QualityTopic, string> = { profiles: 'profile', sizes: 'groessen', formats: 'formate' }

export function qualityTopicFromAddress(value: string | null): QualityTopic {
  return QUALITY_TOPICS.find((topic) => QUALITY_TOPIC_TO_ADDRESS[topic] === value) ?? 'profiles'
}

export function addressOfQualityTopic(topic: QualityTopic): string {
  return QUALITY_TOPIC_TO_ADDRESS[topic]
}

/** Der Reiter "Qualitaet" auf den Profilen, etwa aus dem Fenster "Qualitaet und Profile holen". */
export const PROFILES_TAB_PATH = '/einstellungen?reiter=qualitaet'

/** Unterreiter unter "Ordner und Benennung": nach Thema. Ordner sind der erste und stehen nicht in der Adresse. */
export type FileTopic = 'folders' | 'naming' | 'rename' | 'subtitles' | 'companions' | 'recycle'

export const FILE_TOPICS: readonly FileTopic[] = ['folders', 'naming', 'rename', 'subtitles', 'companions', 'recycle']

const FILE_TOPIC_TO_ADDRESS: Record<FileTopic, string> = {
  folders: 'ordner',
  naming: 'benennung',
  rename: 'umbenennen',
  subtitles: 'untertitel',
  companions: 'begleitdateien',
  recycle: 'papierkorb',
}

/** Die alte Adresse `?reiter=benennung` fuehrt weiter zur Benennung. */
export function fileTopicFromAddress(value: string | null, tabAddress: string | null = null): FileTopic {
  return FILE_TOPICS.find((topic) => FILE_TOPIC_TO_ADDRESS[topic] === value) ?? (tabAddress === 'benennung' ? 'naming' : 'folders')
}

export function addressOfFileTopic(topic: FileTopic): string {
  return FILE_TOPIC_TO_ADDRESS[topic]
}

/** Die Medienart innerhalb eines Themas steht als `art` in der Adresse, Filme nicht. */
export const KIND_PARAM = 'art'

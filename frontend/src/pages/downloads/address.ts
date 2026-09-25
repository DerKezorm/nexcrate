import type { DownloadsTab } from '../../api/types'

export const DOWNLOADS_PATH = '/downloads'

/** Der Reiter Probleme, etwa von einer Fassung auf der Titelseite, deren Download feststeckt. */
export const DOWNLOADS_PROBLEMS_PATH = '/downloads?reiter=probleme'

/** Deutsche Woerter in der Adresse, englische Werte im Code, wie bei den Einstellungen. */
const FROM_ADDRESS: Record<string, DownloadsTab> = { aktiv: 'active', probleme: 'problems', wartet: 'waiting', verlauf: 'history', sperrliste: 'blocklist' }

const TO_ADDRESS: Record<DownloadsTab, string> = { active: 'aktiv', problems: 'probleme', waiting: 'wartet', history: 'verlauf', blocklist: 'sperrliste' }

export function viewFromAddress(value: string | null): DownloadsTab {
  return value !== null && Object.hasOwn(FROM_ADDRESS, value) ? FROM_ADDRESS[value] : 'active'
}

export function addressOfView(view: DownloadsTab): string {
  return TO_ADDRESS[view]
}

/** Der Reiter "Wartet": Releases, die ihre Wartezeit absitzen, etwa von einer Fassung aus. */
export const DOWNLOADS_WAITING_PATH = '/downloads?reiter=wartet'

/** Die Sperrliste, etwa von einem fehlgeschlagenen Download aus. */
export const DOWNLOADS_BLOCKLIST_PATH = '/downloads?reiter=sperrliste'

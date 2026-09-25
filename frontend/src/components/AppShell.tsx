import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { NavLink, Outlet, useLocation } from 'react-router-dom'

import { systemSettingsApi } from '../api/outside'
import { systemApi } from '../api/system'
import { Logo } from './Logo'
import { LogoutButton } from './LogoutButton'
import { NoticeProvider } from './NoticeProvider'
import { PairingBanner } from './PairingBanner'
import { LanguageSwitcher, ThemeSwitcher } from './Switchers'
import { Symbol, type SymbolName } from './Symbol'
import { WhatsNewAfterUpdate } from './WhatsNewAfterUpdate'

/** `right`: steht am rechten Rand der Leiste, vor den Umschaltern. */
type NavItem = { to: string; label: string; symbol: SymbolName; end: boolean; right?: boolean }

function navClass(isActive: boolean, compact = false, right = false): string {
  // Kompakt (unter lg): auf dem Handy vier gleich breite Spalten, Symbol ueber der Beschriftung; in einer Zeile
  // mit Pillen waren es 512 px, "Einstellungen" lag bei 375 px unsichtbar rechts ausserhalb (gemessen 24.09.2026).
  const shape = compact
    ? 'min-w-0 flex-col gap-0.5 rounded-xl px-1 py-1.5 text-[11px] sm:shrink-0 sm:flex-row sm:gap-2 sm:rounded-full sm:px-3 sm:text-sm ' +
      (right ? 'sm:ml-auto ' : '')
    : 'gap-2 rounded-full px-3.5 py-1.5 text-sm ' + (right ? 'ml-auto ' : '')
  return (
    shape +
    'inline-flex items-center font-medium transition-colors ' +
    (isActive ? 'bg-accent-500/15 text-accent-400' : 'text-mist-500 hover:bg-ink-850 hover:text-mist-100')
  )
}

/**
 * Rahmen der Anwendung, gebaut wie Nexview und nexbeat: Kopfzeile, Pillen, Inhalt, Fusszeile.
 * Drei Bereiche: Bibliothek und Downloads links, Einstellungen rechts. Der Import aus Radarr ist ein
 * Helfer unter Einstellungen, kein eigener Menuepunkt.
 */
export function AppShell() {
  const { t } = useTranslation()
  const { pathname } = useLocation()
  const [version, setVersion] = useState<string | null>(null)
  const [updateAvailable, setUpdateAvailable] = useState(false)

  // Version und Update-Hinweis fuer die Fusszeile; scheitert eine Abfrage, fehlt nur der Teil.
  useEffect(() => {
    let current = true
    systemApi.health().then(
      (health) => current && setVersion(typeof health?.version === 'string' ? health.version : null),
      () => undefined,
    )
    systemSettingsApi.read().then(
      (settings) => current && setUpdateAvailable(settings?.update?.available === true),
      () => undefined,
    )
    return () => {
      current = false
    }
  }, [])

  const items: NavItem[] = [
    { to: '/', label: t('common.nav.library'), symbol: 'library', end: true },
    { to: '/kalender', label: t('common.nav.calendar'), symbol: 'calendar', end: false },
    { to: '/downloads', label: t('common.nav.downloads'), symbol: 'download', end: false },
    { to: '/einstellungen', label: t('common.nav.settings'), symbol: 'settings', end: false, right: true },
  ]
  // Ein Titel gehoert zur Bibliothek, auch wenn seine Adresse anders anfaengt. The folder page "Ordner einlesen" too.
  const active = (to: string, isActive: boolean) => isActive || (to === '/' && (pathname.startsWith('/titel/') || pathname.startsWith('/ordner')))

  const renderItem = (item: NavItem, compact: boolean) => (
    <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => navClass(active(item.to, isActive), compact, item.right)}>
      <Symbol name={item.symbol} />
      <span className="max-w-full truncate">{item.label}</span>
    </NavLink>
  )

  return (
    <NoticeProvider>
      <div className="nc-glow flex min-h-dvh flex-col">
        <header className="sticky top-0 z-20 border-b border-ink-700/80 bg-ink-950/80 backdrop-blur-xl">
          <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-3 sm:px-6">
            <NavLink to="/" className="shrink-0" aria-label={t('common.nav.home')}>
              <Logo withWordmark />
            </NavLink>
            <nav className="hidden flex-1 items-center gap-1 lg:flex" aria-label={t('common.nav.main')}>
              {items.map((item) => renderItem(item, false))}
            </nav>
            <div className="ml-auto flex items-center gap-2 sm:gap-3">
              <ThemeSwitcher />
              <LanguageSwitcher />
              <LogoutButton />
            </div>
          </div>
          <nav
            className="grid grid-cols-4 border-t border-ink-700/60 px-1 py-1.5 sm:flex sm:gap-1 sm:overflow-x-auto sm:px-4 sm:py-2 lg:hidden"
            aria-label={t('common.nav.main')}
          >
            {items.map((item) => renderItem(item, true))}
          </nav>
        </header>

        <main className="relative z-10 mx-auto w-full max-w-7xl flex-1 px-4 pt-8 pb-28 sm:px-6">
          <div className="mb-6 empty:hidden">
            <PairingBanner />
          </div>
          <Outlet />
        </main>
        <WhatsNewAfterUpdate />

        <footer className="relative z-10 border-t border-ink-700/60">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-center gap-x-4 gap-y-2 px-4 py-5 text-xs text-mist-600 sm:px-6">
            {/* Wie in Nexview und nexbeat: "Ueber" steht hier, nicht in den Einstellungen. */}
            <NavLink to="/ueber" className="transition-colors hover:text-mist-300">
              {t('common.footer.about')}
            </NavLink>
            {version && (
              <>
                <span aria-hidden="true">·</span>
                <span className="tabular-nums">v{version}</span>
              </>
            )}
            <span aria-hidden="true">·</span>
            <span>{t('common.footer.tagline')}</span>
            {updateAvailable && (
              <NavLink
                to="/ueber"
                className="inline-flex items-center gap-1.5 rounded-full bg-accent-500/15 px-2.5 py-1 font-medium text-accent-400 transition-colors hover:bg-accent-500/25"
              >
                <span className="h-1.5 w-1.5 rounded-full bg-accent-400" aria-hidden="true" />
                {t('common.footer.update')}
              </NavLink>
            )}
          </div>
        </footer>
      </div>
    </NoticeProvider>
  )
}

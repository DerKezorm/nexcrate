import { useState } from 'react'
import type { ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText } from '../api/client'
import { useAuth } from '../auth/useAuth'
import { isLanguage, LANGUAGES, type Language } from '../i18n/languages'
import { applyTheme, storedTheme, type Theme } from '../lib/theme'
import { Symbol } from './Symbol'
import { useNotice } from './useNotice'

/** Mehr Sprachknoepfe passen oben am Telefon nicht nebeneinander. Darueber wird es eine Auswahlliste. */
const LANGUAGE_BUTTONS_MAX = 3

/**
 * Sprachwahl in der Kopfzeile der App. Die Liste kommt aus `i18n/languages.ts`.
 * Angemeldet merkt sich der Server die Wahl fuers Konto.
 */
export function LanguageSwitcher() {
  const { t, i18n } = useTranslation()
  const { setLanguage } = useAuth()
  const notify = useNotice()
  const onError = (error: unknown) => notify(errorText(t, error))

  if (LANGUAGES.length > LANGUAGE_BUTTONS_MAX) return <LanguageSelect onError={onError} />

  return (
    <div className="flex items-center rounded-full border border-ink-700 bg-ink-850 p-0.5" role="group" aria-label={t('common.language.label')}>
      {LANGUAGES.map(({ code, name }) => {
        const active = i18n.language === code
        return (
          <button
            key={code}
            type="button"
            lang={code}
            title={name}
            onClick={() => setLanguage(code).catch(onError)}
            aria-pressed={active}
            className={
              'rounded-full px-2.5 py-1 text-xs font-semibold uppercase transition-colors ' +
              (active ? 'bg-accent-500 text-on-accent' : 'text-mist-500 hover:text-mist-100')
            }
          >
            {code}
          </button>
        )
      })}
    </div>
  )
}

/** Sprachwahl als Liste mit den Namen der Sprachen, fuer die Seiten vor der Anmeldung. */
export function LanguageSelect({ onError }: { onError?: (error: unknown) => void }) {
  const { t, i18n } = useTranslation()
  const { setLanguage } = useAuth()

  function select(value: string) {
    if (!isLanguage(value)) return
    const language: Language = value
    setLanguage(language).catch((error: unknown) => onError?.(error))
  }

  return (
    <label className="relative inline-flex items-center">
      <span className="sr-only">{t('common.language.label')}</span>
      <Symbol name="globe" className="pointer-events-none absolute left-2.5 h-4 w-4 text-mist-500" />
      <select
        value={i18n.language}
        onChange={(event) => select(event.target.value)}
        className="appearance-none rounded-full border border-ink-700 bg-ink-850 py-1.5 pr-8 pl-8 text-sm font-medium text-mist-200 transition-colors hover:text-mist-100 focus:border-accent-500 focus:outline-none"
      >
        {LANGUAGES.map(({ code, name }) => (
          <option key={code} value={code} lang={code}>
            {name}
          </option>
        ))}
      </select>
      <Symbol name="chevronDown" className="pointer-events-none absolute right-2.5 h-4 w-4 text-mist-500" />
    </label>
  )
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-3.5 w-3.5" fill="currentColor" aria-hidden="true">
      <path d="M17.3 12.9A7.5 7.5 0 0 1 7.1 2.7a.8.8 0 0 0-1-1 9.1 9.1 0 1 0 12.2 12.2.8.8 0 0 0-1-1Z" />
    </svg>
  )
}

function SunIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-3.5 w-3.5" fill="currentColor" aria-hidden="true">
      <path d="M10 14a4 4 0 1 1 0-8 4 4 0 0 1 0 8Zm0-10.8a.9.9 0 0 1-.9-.9V1.9a.9.9 0 0 1 1.8 0v.4a.9.9 0 0 1-.9.9Zm0 15.6a.9.9 0 0 1-.9-.9v-.4a.9.9 0 0 1 1.8 0v.4a.9.9 0 0 1-.9.9ZM18.1 10.9h-.4a.9.9 0 0 1 0-1.8h.4a.9.9 0 0 1 0 1.8Zm-15.8 0h-.4a.9.9 0 0 1 0-1.8h.4a.9.9 0 0 1 0 1.8Z" />
    </svg>
  )
}

/** Hell/Dunkel. Die Wahl bleibt im Browser. */
export function ThemeSwitcher() {
  const { t } = useTranslation()
  const [theme, setTheme] = useState<Theme>(storedTheme())

  function select(next: Theme) {
    applyTheme(next)
    setTheme(next)
  }

  const modes: { value: Theme; label: string; icon: () => ReactElement }[] = [
    { value: 'dark', label: t('common.theme.dark'), icon: MoonIcon },
    { value: 'light', label: t('common.theme.light'), icon: SunIcon },
  ]

  return (
    <div className="flex items-center rounded-full border border-ink-700 bg-ink-850 p-0.5" role="group" aria-label={t('common.theme.label')}>
      {modes.map(({ value, label, icon: Icon }) => (
        <button
          key={value}
          type="button"
          onClick={() => select(value)}
          aria-pressed={theme === value}
          title={label}
          aria-label={label}
          className={'rounded-full p-1.5 transition-colors ' + (theme === value ? 'bg-accent-500 text-on-accent' : 'text-mist-500 hover:text-mist-100')}
        >
          <Icon />
        </button>
      ))}
    </div>
  )
}

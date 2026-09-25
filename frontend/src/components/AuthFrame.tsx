import type { ReactNode } from 'react'

import { Logo } from './Logo'
import { LanguageSelect, ThemeSwitcher } from './Switchers'

/**
 * Rahmen fuer alles vor der Anmeldung: Zeichen, eine schmale Karte, oben rechts
 * Darstellung und Sprache. Die Einrichtung gibt dem Konto die Sprache, die hier
 * oben gerade gewaehlt ist; ein eigenes Feld dafuer gibt es nicht.
 */
export function AuthFrame({ children }: { children: ReactNode }) {
  return (
    <div className="nc-glow flex min-h-dvh flex-col">
      <header className="relative z-10 flex items-center justify-end gap-2 px-4 py-3 sm:px-6">
        <ThemeSwitcher />
        <LanguageSelect />
      </header>
      <main className="relative z-10 flex flex-1 justify-center px-4 pt-2 pb-16 sm:items-center sm:pb-24">
        <div className="animate-nc-rise w-full max-w-md">
          <div className="mb-6 flex flex-col items-center gap-3">
            <Logo className="h-14 w-14" />
            <p className="text-2xl font-bold tracking-tight">
              NEX<span className="text-accent-500">CRATE</span>
            </p>
          </div>
          {children}
        </div>
      </main>
    </div>
  )
}

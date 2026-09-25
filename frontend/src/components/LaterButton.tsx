import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { Symbol } from './Symbol'
import { Button } from './ui'
import { useLaterNotice } from './useLaterNotice'

/**
 * Knopf fuer etwas, das erst ein spaeterer Schritt kann: suchen, laden, aendern,
 * entfernen. Er ist gedimmt, traegt eine kleine Uhr, sagt beim Klick, dass es
 * spaeter kommt, und tut sonst nichts. Nie ein vorgetaeuschter Erfolg.
 */
export function LaterButton({
  children,
  size = 'md',
  label,
  className = '',
}: {
  children: ReactNode
  size?: 'md' | 'sm'
  /** Name fuer Vorleseprogramme, wenn der sichtbare Text allein nicht reicht. Muss den sichtbaren Text enthalten. */
  label?: string
  className?: string
}) {
  const { t } = useTranslation()
  const notify = useLaterNotice()
  return (
    <Button variant="ghost" size={size} aria-disabled="true" aria-label={label} title={t('common.later.title')} onClick={notify} className={'opacity-75 ' + className}>
      {children}
      <Symbol name="clock" className="h-3.5 w-3.5 text-info-500" />
    </Button>
  )
}

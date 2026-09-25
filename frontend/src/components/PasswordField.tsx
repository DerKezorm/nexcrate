import { useId, useState } from 'react'
import type { InputHTMLAttributes, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { Symbol } from './Symbol'

type PasswordFieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & {
  label: string
  hint?: ReactNode
}

/**
 * Passwortfeld mit Knopf zum Anzeigen. Die Beschriftung des Knopfs bleibt
 * gleich, `aria-pressed` sagt, ob das Passwort gerade sichtbar ist.
 */
export function PasswordField({ label, hint, className = '', ...rest }: PasswordFieldProps) {
  const { t } = useTranslation()
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  const [visible, setVisible] = useState(false)

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-mist-300">
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          type={visible ? 'text' : 'password'}
          aria-describedby={hintId}
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          className={
            'w-full rounded-xl border border-ink-700 bg-ink-900 py-2.5 pr-12 pl-4 text-mist-100 ' +
            'placeholder:text-mist-600 transition-colors focus:border-accent-500 focus:outline-none ' +
            className
          }
          {...rest}
        />
        <button
          type="button"
          onClick={() => setVisible((current) => !current)}
          aria-pressed={visible}
          aria-controls={id}
          aria-label={t('common.password.show')}
          title={t('common.password.show')}
          className="absolute inset-y-0 right-0 flex w-11 items-center justify-center rounded-r-xl text-mist-500 transition-colors hover:text-mist-100"
        >
          <Symbol name={visible ? 'eyeOff' : 'eye'} className="h-5 w-5" />
        </button>
      </div>
      {hint && (
        <p id={hintId} className="text-xs text-mist-500">
          {hint}
        </p>
      )}
    </div>
  )
}

/** Grundbausteine im Look von Nexview und nexbeat, mit Bernstein als Akzent. */

import type { ComponentPropsWithRef, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from 'react'
import { useId } from 'react'
import { useTranslation } from 'react-i18next'

import { buttonClasses, type ButtonSize, type ButtonVariant } from './buttonClasses'
import { Symbol } from './Symbol'

type ButtonProps = ComponentPropsWithRef<'button'> & {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
}

export function Button({ variant = 'primary', size = 'md', loading = false, className = '', children, disabled, ...rest }: ButtonProps) {
  return (
    <button type="button" className={`${buttonClasses(variant, size)} ${className}`} disabled={disabled || loading} {...rest}>
      {loading && <Spinner />}
      {children}
    </button>
  )
}

export function Spinner({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg className={`${className} animate-spin`} viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" fill="none" opacity=".25" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" />
    </svg>
  )
}

export function PageLoading() {
  const { t } = useTranslation()
  return (
    <div className="flex min-h-[40vh] items-center justify-center gap-2 text-sm text-mist-600" role="status" aria-live="polite">
      <Spinner />
      <span>{t('common.loading')}</span>
    </div>
  )
}

type FieldProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string
  hint?: ReactNode
}

export function Field({ label, hint, className = '', ...rest }: FieldProps) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-mist-300">
        {label}
      </label>
      <input
        id={id}
        aria-describedby={hintId}
        className={
          'rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-mist-100 ' +
          'placeholder:text-mist-600 transition-colors focus:border-accent-500 focus:outline-none ' +
          className
        }
        {...rest}
      />
      {hint && (
        <p id={hintId} className="text-xs text-mist-500">
          {hint}
        </p>
      )}
    </div>
  )
}

type SelectFieldProps = SelectHTMLAttributes<HTMLSelectElement> & {
  label: string
  hint?: ReactNode
  children: ReactNode
}

export function SelectField({ label, hint, className = '', children, ...rest }: SelectFieldProps) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-mist-300">
        {label}
      </label>
      <select
        id={id}
        aria-describedby={hintId}
        className={
          'rounded-xl border border-ink-700 bg-ink-900 px-4 py-2.5 text-sm text-mist-100 ' +
          'focus:border-accent-500 focus:outline-none disabled:opacity-50 ' +
          className
        }
        {...rest}
      >
        {children}
      </select>
      {hint && (
        <p id={hintId} className="text-xs text-mist-500">
          {hint}
        </p>
      )}
    </div>
  )
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={'rounded-2xl border border-ink-700 bg-ink-850/80 p-6 shadow-2xl shadow-black/30 backdrop-blur ' + className}>{children}</div>
}

/** Ein abgegrenzter Bereich: Ueberschrift, Erklaerung, Inhalt in einer Karte. */
export function Section({
  title,
  intro,
  actions,
  children,
  className = '',
}: {
  title: string
  intro?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <Card className={'flex flex-col gap-4 ' + className}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{title}</h2>
          {intro && <p className="mt-1 max-w-3xl text-sm text-mist-500">{intro}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </Card>
  )
}

/** Eine grosse Zahl mit Beschriftung. */
export function KeyFigure({
  label,
  value,
  hint,
  second,
}: {
  label: string
  value: string
  hint?: string
  /** Eine zweite Zahl in derselben Kachel, etwa die Folgen neben den Serien. */
  second?: { label: string; value: string }
}) {
  return (
    <div className="rounded-2xl border border-ink-700 bg-ink-850/60 px-4 py-3">
      <p className="text-xs font-medium tracking-wide text-mist-600 uppercase">
        {label}
        {second && <span className="text-mist-700"> | </span>}
        {second?.label}
      </p>
      <p className="mt-1 flex items-baseline gap-2 text-3xl font-bold text-mist-100 tabular-nums">
        {value}
        {second && (
          <>
            <span className="text-mist-700">|</span>
            {second.value}
          </>
        )}
      </p>
      {hint && <p className="mt-0.5 text-xs text-mist-600">{hint}</p>}
    </div>
  )
}

/** Seitentitel mit Punkt im Akzent, wie in Nexview. */
export function PageTitle({ children, sub, actions }: { children: ReactNode; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
          {children}
          <span className="text-accent-500">.</span>
        </h1>
        {sub && <p className="mt-2 max-w-2xl text-sm text-mist-500">{sub}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  )
}

/** Ein Schalter als Kontrollkaestchen, mit Erklaerung darunter. */
export function Toggle({
  label,
  hint,
  checked,
  onChange,
  disabled = false,
}: {
  label: string
  hint?: ReactNode
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
}) {
  const id = useId()
  return (
    <div className="flex items-start gap-3">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-1 h-4 w-4 accent-accent-500"
      />
      <label htmlFor={id} className="flex flex-col gap-0.5">
        <span className="text-sm font-medium text-mist-200">{label}</span>
        {hint && <span className="text-xs text-mist-500">{hint}</span>}
      </label>
    </div>
  )
}

/**
 * Ein Schalter mit sichtbarer Beschriftung. ⚠️ `<label for>` gibt einem Knopf keinen Namen,
 * nur Formularfeldern. Der Knopf zeigt deshalb mit `aria-labelledby` auf die Beschriftung;
 * das Label bleibt, damit ein Klick darauf ebenfalls schaltet.
 */
export function Switch({
  label,
  hint,
  checked,
  onChange,
  disabled = false,
}: {
  label: string
  hint?: ReactNode
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
}) {
  const id = useId()
  const labelId = `${id}-label`
  const hintId = hint ? `${id}-hint` : undefined
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="flex min-w-0 flex-col gap-0.5">
        <label id={labelId} htmlFor={id} className="text-sm font-medium text-mist-200">
          {label}
        </label>
        {hint && (
          <p id={hintId} className="text-xs text-mist-500">
            {hint}
          </p>
        )}
      </div>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={labelId}
        aria-describedby={hintId}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={
          'relative mt-0.5 inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors disabled:cursor-not-allowed disabled:opacity-60 ' +
          (checked ? 'border-accent-500 bg-accent-500' : 'border-ink-600 bg-ink-700')
        }
      >
        <span
          aria-hidden="true"
          className={'inline-block h-4 w-4 rounded-full transition-transform ' + (checked ? 'translate-x-6 bg-ink-900' : 'translate-x-1 bg-mist-500')}
        />
      </button>
    </div>
  )
}

/** Fortschrittsbalken mit Zahl fuer Vorleseprogramme. */
export function ProgressBar({ value, tone = 'info', label }: { value: number; tone?: 'info' | 'ok' | 'accent' | 'bad'; label: string }) {
  const color = tone === 'ok' ? 'bg-ok-500' : tone === 'accent' ? 'bg-accent-500' : tone === 'bad' ? 'bg-bad-500' : 'bg-info-500'
  const percent = Math.round(Math.min(1, Math.max(0, value)) * 100)
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-ink-700"
      role="progressbar"
      aria-label={label}
      aria-valuenow={percent}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div className={'h-full rounded-full ' + color} style={{ width: `${percent}%` }} />
    </div>
  )
}

/**
 * Kleine Marke fuer Text. `tone` waehlt die Farbe, nie eine eigene erfinden. `accent` ist Bernstein,
 * getoent statt gefuellt, deshalb Schrift in `accent-400` und nicht `on-accent`. `describedBy` zeigt
 * auf einen Satz, der die Marke erklaert.
 */
export function Badge({ children, tone = 'neutral', describedBy }: { children: ReactNode; tone?: 'neutral' | 'ok' | 'info' | 'bad' | 'accent'; describedBy?: string }) {
  const styles = {
    neutral: 'border-ink-600 bg-ink-900 text-mist-400',
    ok: 'border-ok-500/40 bg-ok-500/10 text-ok-500',
    info: 'border-info-500/40 bg-info-500/10 text-info-500',
    bad: 'border-bad-500/40 bg-bad-500/10 text-bad-500',
    accent: 'border-accent-500/40 bg-accent-500/10 text-accent-400',
  }[tone]
  return (
    <span aria-describedby={describedBy} className={'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold ' + styles}>
      {children}
    </span>
  )
}

/**
 * Rueckmeldung in einem Formular. Fehler werden sofort vorgelesen, Hinweise
 * hoeflich. `role` ueberschreibt das, etwa `timer` fuer einen Countdown, der
 * nicht jede Sekunde neu angesagt werden soll.
 */
export function FormMessage({ tone = 'bad', role, children }: { tone?: 'bad' | 'ok' | 'info'; role?: string; children: ReactNode }) {
  const styles = {
    bad: 'border-bad-500/40 bg-bad-500/10 text-bad-500',
    ok: 'border-ok-500/40 bg-ok-500/10 text-ok-500',
    info: 'border-info-500/40 bg-info-500/5 text-mist-200',
  }[tone]
  return (
    <div role={role ?? (tone === 'bad' ? 'alert' : 'status')} className={'flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5 text-sm ' + styles}>
      <Symbol name={tone === 'bad' ? 'alert' : tone === 'ok' ? 'check' : 'info'} className={'mt-0.5 h-4 w-4 shrink-0 ' + (tone === 'info' ? 'text-info-500' : '')} />
      <p className="min-w-0 wrap-anywhere">{children}</p>
    </div>
  )
}

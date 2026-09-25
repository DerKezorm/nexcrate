export type ButtonVariant = 'primary' | 'ghost' | 'danger'
export type ButtonSize = 'md' | 'sm'

/** Das Aussehen eines Knopfes, auch fuer einen Link, der wie ein Knopf aussehen soll (etwa ein Download). */
export function buttonClasses(variant: ButtonVariant = 'primary', size: ButtonSize = 'md'): string {
  const base =
    'inline-flex items-center justify-center gap-2 rounded-full font-semibold transition-colors ' +
    'disabled:cursor-not-allowed disabled:opacity-60 ' +
    (size === 'sm' ? 'px-3.5 py-1.5 text-xs ' : 'px-5 py-2.5 text-sm ')
  const styles =
    variant === 'primary'
      ? 'bg-accent-500 text-on-accent hover:bg-accent-400 shadow-lg shadow-accent-700/20'
      : variant === 'danger'
        ? 'border border-bad-500/40 bg-bad-500/10 text-bad-500 hover:bg-bad-500/20'
        : 'border border-ink-700 bg-ink-850 text-mist-300 hover:bg-ink-800 hover:text-mist-100'
  return `${base} ${styles}`
}

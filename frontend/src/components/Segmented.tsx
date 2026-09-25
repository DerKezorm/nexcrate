/** Eine geschlossene Leiste, von der genau ein Knopf aktiv ist. Wie `Umschalter` in Nexview. */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  ariaLabel,
}: {
  value: T
  options: readonly T[]
  onChange: (next: T) => void
  label: (option: T) => string
  ariaLabel?: string
}) {
  return (
    <div className="inline-flex w-fit flex-wrap rounded-full border border-ink-700 bg-ink-850 p-1" role="group" aria-label={ariaLabel}>
      {options.map((option) => (
        <button
          key={option}
          type="button"
          aria-pressed={value === option}
          onClick={() => onChange(option)}
          className={
            'rounded-full px-4 py-1.5 text-sm font-semibold transition-colors ' +
            (value === option ? 'bg-accent-500 text-on-accent' : 'text-mist-500 hover:text-mist-300')
          }
        >
          {label(option)}
        </button>
      ))}
    </div>
  )
}

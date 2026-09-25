import { useId } from 'react'

/**
 * Ein kleiner Schalter mit Text daneben, fuer Staffeln und Folgen. ⚠️ `<label for>` benennt keinen Knopf: Ohne
 * `label` zeigt der Knopf mit `aria-labelledby` auf den sichtbaren Text, mit `label` traegt er diesen Namen.
 */
export function InlineSwitch({
  text,
  label,
  checked,
  disabled = false,
  onChange,
}: {
  text: string
  label?: string
  checked: boolean
  disabled?: boolean
  onChange: (checked: boolean) => void
}) {
  const id = useId()
  return (
    <span className="inline-flex min-w-0 items-center gap-2">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        aria-labelledby={label === undefined ? id : undefined}
        disabled={disabled}
        onClick={(event) => {
          // Der Schalter sitzt in klickbaren Zeilen. Die Zeile soll dabei nicht auf- oder zugehen.
          event.stopPropagation()
          onChange(!checked)
        }}
        className={
          'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors disabled:cursor-not-allowed disabled:opacity-60 ' +
          (checked ? 'border-accent-500 bg-accent-500' : 'border-ink-600 bg-ink-700')
        }
      >
        <span
          aria-hidden="true"
          className={'inline-block h-3.5 w-3.5 rounded-full transition-transform ' + (checked ? 'translate-x-4 bg-ink-900' : 'translate-x-0.5 bg-mist-500')}
        />
      </button>
      <span id={id} className="min-w-0 text-sm text-mist-300 wrap-anywhere">
        {text}
      </span>
    </span>
  )
}

import type { TFunction } from 'i18next'

import type { FormatCondition, FormatSpecification } from '../../api/types'

/**
 * Die Bedingungen einer Formatregel. Der Server sagt, welche es je Art gibt und welche Zahlen die Werkzeuge
 * vergleichen (`GET /api/custom-formats/conditions`); hier stehen nur die Namen dafuer und die kleinen
 * Umrechnungen zwischen Feld und Eingabe.
 */

/** Der Name einer Bedingung. Eine, die die Oberflaeche noch nicht kennt, behaelt ihren technischen Namen. */
export function conditionName(t: TFunction, implementation: string): string {
  const key = `quality.conditions.${implementation}`
  const text = t(key)
  return text === key ? implementation.replace(/Specification$/, '') : text
}

export function conditionOf(conditions: FormatCondition[], implementation: string): FormatCondition | null {
  return conditions.find((entry) => entry.implementation === implementation) ?? null
}

/** Eine neue Bedingung dieser Art, mit leeren Feldern. */
export function emptySpecification(condition: FormatCondition): FormatSpecification {
  const fields =
    condition.value === 'range' ? { min: 0, max: 0 } : condition.value === 'choice' ? { value: condition.options[0]?.value ?? 0 } : { value: '' }
  return { implementation: condition.implementation, negate: false, required: false, fields }
}

export function fieldOf(specification: FormatSpecification, name: string): unknown {
  const fields = specification.fields
  return fields === undefined || fields === null ? undefined : fields[name]
}

export function withField(specification: FormatSpecification, name: string, value: unknown): FormatSpecification {
  return { ...specification, fields: { ...(specification.fields ?? {}), [name]: value } }
}

/**
 * Die Bedingungen eines Formats in einer Zeile: je Art einmal, mit der Zahl dahinter, wenn es mehrere sind.
 * Drei Muster auf den Release-Titel sind eine Gruppe und lesen sich sonst als dreimal dasselbe.
 */
export function conditionSummary(t: TFunction, specifications: FormatSpecification[]): string {
  const counted = new Map<string, number>()
  for (const specification of specifications) {
    const name = conditionName(t, specification.implementation)
    counted.set(name, (counted.get(name) ?? 0) + 1)
  }
  return [...counted].map(([name, count]) => (count === 1 ? name : `${name} (${count})`)).join(', ')
}

/** Ein Muster ohne Inhalt trifft nie; der Dialog laesst es deshalb nicht speichern. */
export function incompleteSpecification(specification: FormatSpecification, condition: FormatCondition | null): boolean {
  if (condition === null) return true
  if (condition.value === 'regex') return String(fieldOf(specification, 'value') ?? '').trim() === ''
  if (condition.value === 'choice') return fieldOf(specification, 'value') === undefined || fieldOf(specification, 'value') === null
  const min = Number(fieldOf(specification, 'min'))
  const max = Number(fieldOf(specification, 'max'))
  return !Number.isFinite(min) || !Number.isFinite(max) || max < min
}

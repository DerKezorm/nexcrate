import type { VersionState } from '../api/types'

/** Kurzer Text zu einem Zustand, ohne Prozentzahl. Die Schluessel stehen woertlich da, damit `keys.test.ts` sie sieht. */
export function versionStateText(t: (key: string) => string, state: VersionState): string {
  switch (state) {
    case 'available':
      return t('common.state.available')
    case 'downloading':
      return t('common.state.downloadingShort')
    case 'wanted':
      return t('common.state.wanted')
    case 'unmonitored':
      return t('common.state.unmonitored')
    case 'problem':
      return t('common.state.problem')
    case 'upgrade':
      return t('common.state.upgrade')
    case 'incomplete':
      return t('common.state.incomplete')
  }
}

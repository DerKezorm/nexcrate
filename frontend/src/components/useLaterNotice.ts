import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'

import { useNotice } from './useNotice'

/** Sagt unten auf dem Bildschirm, dass ein Knopf erst in einem spaeteren Schritt etwas tut. */
export function useLaterNotice(): () => void {
  const { t } = useTranslation()
  const notify = useNotice()
  return useCallback(() => notify(t('common.later.notice')), [notify, t])
}

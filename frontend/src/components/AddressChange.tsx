import { useTranslation } from 'react-i18next'

import type { AddressQuestion } from '../lib/addressQuestion'
import { Dialog } from './Dialog'
import { Button } from './ui'

/** Fragt, bevor die neue oeffentliche Adresse gespeichert wird; Abbrechen laesst alles, wie es ist. */
export function AddressChangeDialog({
  question,
  busy = false,
  onCancel,
  onConfirm,
}: {
  question: AddressQuestion | null
  busy?: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  const { t } = useTranslation()
  return (
    <Dialog
      open={question !== null}
      title={t('oidc.admin.address')}
      onClose={onCancel}
      footer={
        <>
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            {t('common.actions.cancel')}
          </Button>
          <Button variant="danger" onClick={onConfirm} loading={busy}>
            {t('common.actions.save')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2 text-sm text-mist-300" data-testid="address-question">
        {question !== null && question.count > 0 && <p>{t('oidc.admin.addressChangeConfirm', { address: question.address })}</p>}
        {question !== null && question.hand > 0 && <p>{t('oidc.admin.addressChangeHand', { count: question.hand })}</p>}
      </div>
    </Dialog>
  )
}

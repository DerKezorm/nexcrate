import { api } from './client'
import type { CompanionJob, CompanionReplaceResult, CompanionsState } from './types'

/** How often the section asks for a running check or backfill while it is open. */
export const COMPANION_POLL_MS = 1000

/**
 * The companion files `release.nex` (L3 of the design notes): the switch, the check that writes
 * nothing, the backfill that writes missing and outdated files, and replacing a file nexcrate would not overwrite.
 */
export const companionsApi = {
  get: () => api.get<CompanionsState>('/companions'),
  save: (enabled: boolean) => api.put<CompanionsState>('/companions', { enabled }),
  /** 202 with the job. 409 `companion_job_running`. */
  check: () => api.post<CompanionJob>('/companions/check'),
  /** 202 with the job. 409 `companion_job_running`, 409 `companions_off`. */
  backfill: () => api.post<CompanionJob>('/companions/backfill'),
  /** The newest job of the last 30 minutes, else 404 `not_found`. */
  job: () => api.get<CompanionJob>('/companions/job'),
  /** Moves the old file into the recycle folder and writes nexcrate's. 404 `not_found`, 409 `companions_off`, 409 `companion_not_replaceable`. */
  replace: (versionId: number) => api.post<CompanionReplaceResult>('/companions/replace', { version_id: versionId }),
}

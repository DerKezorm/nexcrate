import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../../api/client'
import { DISK_POLL_MS, diskApi } from '../../api/disk'
import type { DiskJob } from '../../api/types'

/**
 * The newest scan, restore or assign job while the page is open, modelled on the takeover's job.
 *
 * The page hands in the job it got with the overview. A running one is asked again every second, each time after the
 * previous answer came. When it ends, `onFinished` runs once, so the page can reload the roots and the rows. A job the
 * server no longer knows (404, after a restart) is lost; other errors keep polling.
 */
export function useDiskJob(initial: DiskJob | null, onFinished: (job: DiskJob) => void) {
  const [job, setJob] = useState<DiskJob | null>(initial)
  const [lost, setLost] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [retry, setRetry] = useState(0)
  const finished = useRef<number | null>(null)
  const onFinishedRef = useRef(onFinished)
  onFinishedRef.current = onFinished

  // The overview may bring a newer job, for example after the page reloaded it. An older snapshot of a job this hook
  // already saw end is not taken: it would start the polling again for nothing.
  useEffect(() => {
    if (initial === null) return
    setJob((current) => (current !== null && current.id === initial.id && current.state !== 'running' ? current : initial))
  }, [initial])

  useEffect(() => {
    if (job === null || job.state !== 'running') return
    let current = true
    const timer = window.setTimeout(() => {
      diskApi.job().then(
        (next) => {
          if (!current) return
          setError(null)
          setJob(next)
          if (next.state !== 'running' && finished.current !== next.id) {
            finished.current = next.id
            onFinishedRef.current(next)
          }
        },
        (problem: unknown) => {
          if (!current) return
          if (problem instanceof ApiError && problem.status === 404) {
            setJob(null)
            setLost(true)
            return
          }
          setError(problem)
          setRetry((count) => count + 1)
        },
      )
    }, DISK_POLL_MS)
    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [job, retry])

  /** A job the server just accepted (202). */
  const follow = useCallback((started: DiskJob) => {
    setLost(false)
    setError(null)
    setJob(started)
  }, [])

  const dismiss = useCallback(() => setJob(null), [])

  return { job, lost, error, follow, dismiss }
}

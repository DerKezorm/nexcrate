import { Component, type ErrorInfo, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../api/client'
import { withBase } from '../lib/base'
import { REPO_URL } from '../pages/settings/AboutSettings'
import { Symbol } from './Symbol'

/** So viele Zeilen des Stapels gehen mit, auf der Seite und ins Protokoll. */
const STACK_LINES = 8

/** Der Fehler als Text: Meldung, darunter die ersten Zeilen des Stapels. */
function crashText(error: Error): string {
  const stack = (error.stack ?? '').split('\n').map((line) => line.trim())
  const lines = stack.filter((line) => line !== '' && !line.endsWith(error.message)).slice(0, STACK_LINES)
  return [error.message || error.name, ...lines].join('\n')
}

/**
 * Schreibt den Absturz ins Protokoll des Servers (`POST /api/logs/client`). Ohne Sitzung oder ohne Server bleibt es
 * still: Ein zweiter Fehler darf die Fehlerseite nicht kippen, und ein 401 fuehrt hier nicht zur Anmeldeseite.
 */
function reportCrash(error: Error, componentStack: string | null | undefined): void {
  const stack = [error.stack ?? '', componentStack ?? ''].join('\n')
  const path = window.location.pathname + window.location.search
  api.post('/logs/client', { message: error.message || error.name, stack: stack.slice(0, 8000), path: path.slice(0, 500) }, { ignoreSessionLost: true }).catch(() => undefined)
}

type Props = {
  children: ReactNode
  /** Aendert sich der Schluessel (eine andere Seite), faengt die Grenze neu an. */
  resetKey?: string
  /** `page`: nur der Inhalt einer Seite, Kopf und Leiste bleiben. `app`: die ganze Oberflaeche. */
  scope?: 'page' | 'app'
}

type State = { error: Error | null }

/**
 * Ein Fehler beim Zeichnen leert in React die ganze Seite: Zurueck bleibt eine weisse Flaeche ohne jedes Wort. Diese
 * Grenze zeigt stattdessen, was passiert ist, schreibt es ins Protokoll und bietet Neuladen und den Weg zur
 * Bibliothek an. Sie steht einmal um die ganze App und einmal um jede Seite.
 */
export class CrashBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: unknown): State {
    return { error: error instanceof Error ? error : new Error(String(error)) }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    reportCrash(error, info.componentStack)
  }

  componentDidUpdate(previous: Props): void {
    if (this.state.error !== null && previous.resetKey !== this.props.resetKey) this.setState({ error: null })
  }

  render() {
    if (this.state.error === null) return this.props.children
    return <CrashPage error={this.state.error} scope={this.props.scope ?? 'page'} />
  }
}

function CrashPage({ error, scope }: { error: Error; scope: 'page' | 'app' }) {
  const { t } = useTranslation()
  return (
    <section role="alert" className={'flex flex-col items-start gap-4 ' + (scope === 'app' ? 'mx-auto max-w-2xl px-4 py-16' : 'py-4')}>
      <Symbol name="alert" className="h-8 w-8 text-bad-500" />
      <h1 className="text-2xl font-semibold text-mist-100">{t('common.crash.title')}</h1>
      <p className="max-w-prose text-sm text-mist-300">{t('common.crash.text')}</p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="inline-flex items-center gap-2 rounded-full bg-accent-500 px-4 py-2 text-sm font-semibold text-on-accent hover:bg-accent-400"
        >
          <Symbol name="refresh" />
          {t('common.crash.reload')}
        </button>
        <a href={withBase('/')} className="inline-flex items-center gap-2 rounded-full border border-ink-700 bg-ink-850 px-4 py-2 text-sm font-semibold text-mist-200 hover:bg-ink-800">
          {t('common.crash.library')}
        </a>
      </div>
      <div className="flex w-full flex-col gap-2">
        <p className="text-xs text-mist-500">
          {t('common.crash.report')}{' '}
          <a href={`${REPO_URL}/issues`} target="_blank" rel="noreferrer" className="font-medium text-accent-400 hover:underline">
            {t('common.crash.issues')}
          </a>
        </p>
        <pre className="w-full overflow-x-auto rounded-xl border border-ink-700 bg-ink-900 p-3 text-xs whitespace-pre-wrap text-mist-300 select-all wrap-anywhere">{crashText(error)}</pre>
      </div>
    </section>
  )
}

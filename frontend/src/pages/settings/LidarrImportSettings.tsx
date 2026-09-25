import { useId, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, errorText } from "../../api/client";
import { sourcesApi } from "../../api/sources";
import type { ImportRun, Source } from "../../api/types";
import { versionsApi } from "../../api/versions";
import { Dialog } from "../../components/Dialog";
import { PasswordField } from "../../components/PasswordField";
import { Symbol } from "../../components/Symbol";
import {
  Badge,
  Button,
  Field,
  FormMessage,
  Section,
  Spinner,
} from "../../components/ui";
import { formatDate, formatDateTime, formatNumber } from "../../lib/format";
import { RadarrClientsFlow } from "../clients/RadarrClientsFlow";
import { RadarrIndexerDialog } from "../import/RadarrIndexers";
import { TakeoverDialog } from "../import/TakeoverDialog";
import { TakeoverUndoDialog } from "../import/TakeoverUndoDialog";
import { useImportRun } from "../import/useImportRun";
import { useSources } from "../import/useSources";
import { useVersions } from "../versions/useVersions";

/** Nur Musik, die dieser Reiter braucht. */
function ensureAlbumVersion(
  existing: { id: number }[] | null,
  label: string,
): Promise<number> {
  if (existing !== null && existing.length > 0)
    return Promise.resolve(existing[0].id);
  return versionsApi
    .create({ kind: "album", label })
    .then((version) => version.id);
}

/**
 * "Lidarr" unter Einstellungen, Import (M1.6): nur lesend, wie Sonarr. Verbindung anlegen (die eine Musik-Fassung
 * entsteht dabei automatisch, Entscheidung 10), pruefen, Lauf starten, Ergebnis mit Kuenstlern, Alben, Ausgaben,
 * Titeln, Dateien und nicht zugeordneten Dateien. Nichts davon geht in Lidarr, es wird nur gelesen.
 *
 * "Aus diesem Lidarr holen" (Musik-Abschluss): Indexer mit ihren Musik-Kategorien und Download-Programme, auch nach
 * der Uebernahme. Die Benennung holt nexcrate aus Lidarr nicht, Musik hat ihre eigene.
 */
export function LidarrImportSettings() {
  const { t } = useTranslation();
  const { sources, error: sourcesError, reload } = useSources();
  // ⚠️ Erst wenn die Liste da ist, weiss der Dialog, ob er die eine Musik-Fassung noch anlegen muss (Entscheidung 10).
  const { versions } = useVersions("album");
  const own = useMemo(
    () =>
      sources === null
        ? null
        : sources.filter((source) => source.app === "lidarr"),
    [sources],
  );
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<Source | null>(null);
  // Musik M6: Übernehmen und Rückgängig wie bei Radarr und Sonarr.
  const [takingOver, setTakingOver] = useState<Source | null>(null);
  const [undoing, setUndoing] = useState<Source | null>(null);
  const [fetching, setFetching] = useState<{
    kind: "indexers" | "clients";
    source: Source;
  } | null>(null);
  // Dieselbe Liste, solange das Fenster offen ist: Die Liste der Download-Programme liest bei jeder neuen neu.
  const fetchSources = useMemo(
    () => (fetching === null ? [] : [fetching.source]),
    [fetching],
  );

  return (
    <Section
      title={t("music.lidarr.title")}
      intro={t("music.lidarr.intro")}
      actions={
        own !== null ? (
          <Button onClick={() => setAdding(true)} disabled={versions === null}>
            <Symbol name="plus" />
            {t("music.lidarr.add")}
          </Button>
        ) : undefined
      }
    >
      <p className="flex items-start gap-2 rounded-xl border border-ok-500/30 bg-ok-500/5 px-4 py-3 text-sm text-mist-300">
        <Symbol name="shield" className="mt-0.5 h-4 w-4 shrink-0 text-ok-500" />
        {t("music.lidarr.readOnly")}
      </p>

      {sourcesError !== null && (
        <FormMessage>{errorText(t, sourcesError)}</FormMessage>
      )}

      {own === null ? (
        sourcesError === null && (
          <p
            className="flex items-center gap-2 py-4 text-sm text-mist-500"
            role="status"
          >
            <Spinner />
            {t("common.loading")}
          </p>
        )
      ) : own.length === 0 ? (
        <p className="text-sm text-mist-500">{t("music.lidarr.empty")}</p>
      ) : (
        <ul className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
          {own.map((source) => (
            <li key={source.id} className="min-w-0">
              <LidarrCard
                source={source}
                onRemove={() => setRemoving(source)}
                onFinished={reload}
                onTakeOver={() => setTakingOver(source)}
                onUndo={() => setUndoing(source)}
                onFetch={(kind) => setFetching({ kind, source })}
              />
            </li>
          ))}
        </ul>
      )}

      {adding && (
        <AddLidarrDialog
          versions={versions}
          onClose={() => setAdding(false)}
          onSaved={() => {
            setAdding(false);
            reload();
          }}
        />
      )}
      {takingOver && (
        <TakeoverDialog
          source={takingOver}
          onClose={() => setTakingOver(null)}
          onTaken={reload}
        />
      )}
      {undoing && (
        <TakeoverUndoDialog
          source={undoing}
          label={null}
          onClose={() => setUndoing(null)}
          onUndone={() => {
            setUndoing(null);
            reload();
          }}
        />
      )}
      {fetching?.kind === "indexers" && (
        <RadarrIndexerDialog
          source={fetching.source}
          onClose={() => setFetching(null)}
        />
      )}
      {fetching?.kind === "clients" && (
        <RadarrClientsFlow
          sources={fetchSources}
          onClose={() => setFetching(null)}
        />
      )}
      {removing && (
        <RemoveLidarrDialog
          source={removing}
          onClose={() => setRemoving(null)}
          onRemoved={() => {
            setRemoving(null);
            reload();
          }}
        />
      )}
    </Section>
  );
}

function LidarrCard({
  source,
  onRemove,
  onFinished,
  onTakeOver,
  onUndo,
  onFetch,
}: {
  source: Source;
  onRemove: () => void;
  onFinished: () => void;
  onTakeOver: () => void;
  onUndo: () => void;
  onFetch: (kind: "indexers" | "clients") => void;
}) {
  const { t, i18n } = useTranslation();
  const takeoverHintId = useId();
  const {
    run,
    error: startError,
    starting,
    start,
  } = useImportRun(source.id, source.last_import, onFinished);
  const [testing, setTesting] = useState(false);
  const [tested, setTested] = useState<string | null>(null);
  const [testProblem, setTestProblem] = useState<unknown>(null);

  async function test() {
    setTesting(true);
    setTested(null);
    setTestProblem(null);
    try {
      const result = await sourcesApi.test({ source_id: source.id });
      setTested(t("music.lidarr.testOk", { version: result.app_version }));
    } catch (error) {
      setTestProblem(error);
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="flex h-full min-w-0 flex-col gap-3 rounded-2xl border border-ink-700 bg-ink-900/60 p-4 sm:p-5">
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-ink-700 bg-ink-850 text-accent-400">
          <Symbol name="note" className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold wrap-anywhere text-mist-100">
            {source.name}
          </h3>
          <p className="text-xs break-all text-mist-500">{source.url}</p>
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {source.has_api_key ? (
          <Badge tone="ok">
            <Symbol name="check" className="h-3.5 w-3.5" />
            {t("import.sources.badgeKey")}
          </Badge>
        ) : (
          <Badge tone="bad">
            <Symbol name="alert" className="h-3.5 w-3.5" />
            {t("import.sources.badgeNoKey")}
          </Badge>
        )}
      </div>
      {source.taken_over_at ? (
        <>
          <p className="text-sm text-mist-300">
            {t("music.lidarr.takenOver", {
              date: formatDate(source.taken_over_at, i18n.language),
            })}
          </p>
          <FormMessage tone="info" role="note">
            {t("music.lidarr.takenOverInfo")}
          </FormMessage>
          <div className="mt-auto flex flex-wrap gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={onUndo}
              aria-label={t("import.takeover.undo.openLabel", {
                name: source.name,
              })}
            >
              <Symbol name="back" />
              {t("import.takeover.undo.open")}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={onRemove}
              aria-label={t("import.sources.removeRecordLabel", {
                name: source.name,
              })}
            >
              {t("import.sources.removeRecord")}
            </Button>
          </div>
          <div className="flex flex-col gap-2 border-t border-ink-700 pt-3">
            <p className="text-xs font-semibold text-mist-400">
              {t("music.lidarr.fetchTitle")}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onFetch("indexers")}
                aria-label={t("import.indexers.openLabel", {
                  name: source.name,
                })}
              >
                <Symbol name="search" />
                {t("import.indexers.open")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onFetch("clients")}
                aria-label={t("import.clients.openLabel", {
                  name: source.name,
                })}
              >
                <Symbol name="download" />
                {t("import.clients.open")}
              </Button>
            </div>
          </div>
        </>
      ) : (
        <>
          <div className="flex flex-col gap-2" aria-live="polite">
            <LidarrRunState run={run} />
            {tested && (
              <div>
                <Badge tone="ok">
                  <Symbol name="check" className="h-3.5 w-3.5" />
                  {tested}
                </Badge>
              </div>
            )}
            {testProblem !== null && (
              <FormMessage>{errorText(t, testProblem)}</FormMessage>
            )}
            {startError !== null && (
              <FormMessage>{errorText(t, startError)}</FormMessage>
            )}
          </div>
          <div className="mt-auto flex flex-wrap gap-2">
            <Button
              size="sm"
              onClick={() => void start()}
              loading={starting}
              disabled={run?.status === "running"}
              aria-label={t("import.run.startLabel", { name: source.name })}
            >
              {!starting && <Symbol name="import" />}
              {t("import.run.start")}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              loading={testing}
              onClick={() => void test()}
              aria-label={t("import.sources.testLabel", { name: source.name })}
            >
              {t("common.actions.test")}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={onRemove}
              disabled={run?.status === "running"}
              aria-label={t("import.sources.removeLabel", {
                name: source.name,
              })}
            >
              {t("common.actions.remove")}
            </Button>
          </div>
          <p className="text-xs text-mist-500">{t("music.lidarr.schedule")}</p>
          <div className="flex flex-col gap-2 border-t border-ink-700 pt-3">
            <p className="text-xs font-semibold text-mist-400">
              {t("music.lidarr.fetchTitle")}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onFetch("indexers")}
                aria-label={t("import.indexers.openLabel", {
                  name: source.name,
                })}
              >
                <Symbol name="search" />
                {t("import.indexers.open")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onFetch("clients")}
                aria-label={t("import.clients.openLabel", {
                  name: source.name,
                })}
              >
                <Symbol name="download" />
                {t("import.clients.open")}
              </Button>
            </div>
          </div>
          <div className="flex flex-col items-start gap-1.5 border-t border-ink-700 pt-3">
            <Button
              size="sm"
              variant="ghost"
              onClick={onTakeOver}
              disabled={run?.status === "running"}
              aria-label={t("import.takeover.openLabel", { name: source.name })}
              aria-describedby={takeoverHintId}
            >
              <Symbol name="swap" />
              {t("import.takeover.open")}
            </Button>
            <p id={takeoverHintId} className="text-xs text-mist-500">
              {t("import.takeover.music.openHint")}
            </p>
          </div>
        </>
      )}
    </div>
  );
}

function LidarrRunState({ run }: { run: ImportRun | null }) {
  const { t, i18n } = useTranslation();
  const language = i18n.language;
  if (run === null)
    return <p className="text-sm text-mist-500">{t("import.run.never")}</p>;
  if (run.status === "running") {
    return (
      <p className="flex items-center gap-2 text-sm text-info-500">
        <Spinner />
        {t("import.run.running")}
      </p>
    );
  }
  const details = run.details ?? null;
  if (run.status === "failed") {
    return (
      <FormMessage>
        {run.error_code
          ? errorText(t, new ApiError(0, run.error_code, run.error_values))
          : t("import.run.failedUnknown")}
      </FormMessage>
    );
  }
  const number = (value: number | undefined) =>
    formatNumber(value ?? 0, language);
  return (
    <div className="flex items-start gap-2 text-sm">
      <Symbol name="check" className="mt-0.5 h-4 w-4 shrink-0 text-ok-500" />
      <div className="min-w-0">
        <p className="text-ok-500">
          {run.finished_at
            ? t("import.run.doneAt", {
                time: formatDateTime(run.finished_at, language),
              })
            : t("import.history.done")}
        </p>
        {details && typeof details.artists === "number" && (
          <>
            <p className="wrap-anywhere text-mist-400">
              {t("music.lidarr.result.counts", {
                artists: number(details.artists),
                albums: number(details.albums),
                releases: number(details.releases),
                tracks: number(details.tracks),
                files: number(details.files),
              })}
            </p>
            {(details.unchanged ?? 0) > 0 && (
              <p className="text-mist-500">
                {t("music.lidarr.result.unchanged", {
                  count: details.unchanged ?? 0,
                  value: number(details.unchanged),
                })}
              </p>
            )}
            {(details.unmapped_files ?? 0) > 0 && (
              <UnmappedFiles
                count={details.unmapped_files ?? 0}
                paths={details.unmapped_paths ?? []}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}

function UnmappedFiles({ count, paths }: { count: number; paths: string[] }) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-1">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="text-xs font-medium text-accent-400 hover:underline"
      >
        {t("music.lidarr.result.unmapped", {
          count,
          value: formatNumber(count, i18n.language),
        })}
      </button>
      {open && paths.length > 0 && (
        <ul className="mt-1 flex flex-col gap-0.5">
          {paths.map((path) => (
            <li
              key={path}
              className="font-mono text-xs wrap-anywhere text-mist-600"
            >
              {path}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function AddLidarrDialog({
  versions,
  onClose,
  onSaved,
}: {
  versions: { id: number }[] | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<unknown>(null);

  async function save() {
    const cleanName = name.trim();
    const cleanUrl = url.trim();
    const key = apiKey.trim();
    if (cleanName === "") return setProblem(t("import.sources.missingName"));
    if (cleanUrl === "") return setProblem(t("import.sources.missingUrl"));
    if (key === "") return setProblem(t("import.sources.missingKey"));
    setBusy(true);
    setProblem(null);
    try {
      const versionId = await ensureAlbumVersion(
        versions,
        t("music.versions.defaultLabel"),
      );
      await sourcesApi.create({
        name: cleanName,
        app: "lidarr",
        url: cleanUrl,
        api_key: key,
        version_id: versionId,
      });
      onSaved();
    } catch (error) {
      setProblem(errorText(t, error));
      setBusy(false);
    }
  }

  function close() {
    if (!busy) onClose();
  }

  return (
    <Dialog
      open
      title={t("music.lidarr.dialogAdd")}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t("common.actions.cancel")}
          </Button>
          <Button onClick={() => void save()} loading={busy}>
            {t("common.actions.save")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field
          label={t("import.sources.name")}
          hint={t("music.lidarr.nameHint")}
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={100}
          autoComplete="off"
          autoFocus
        />
        <Field
          label={t("import.sources.url")}
          hint={t("music.lidarr.urlHint")}
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          inputMode="url"
          maxLength={2048}
          autoComplete="off"
          spellCheck={false}
          className="w-full min-w-0"
        />
        <PasswordField
          label={t("import.sources.apiKey")}
          hint={t("music.lidarr.apiKeyHint")}
          value={apiKey}
          onChange={(event) => setApiKey(event.target.value)}
          autoComplete="new-password"
          maxLength={200}
        />
        {problem !== null && typeof problem === "string" && (
          <FormMessage>{problem}</FormMessage>
        )}
        {problem !== null && typeof problem !== "string" && (
          <FormMessage>{errorText(t, problem)}</FormMessage>
        )}
      </div>
    </Dialog>
  );
}

function RemoveLidarrDialog({
  source,
  onClose,
  onRemoved,
}: {
  source: Source;
  onClose: () => void;
  onRemoved: () => void;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<unknown>(null);

  async function remove() {
    setBusy(true);
    setProblem(null);
    try {
      await sourcesApi.remove(source.id);
      onRemoved();
    } catch (error) {
      setProblem(error);
      setBusy(false);
    }
  }

  function close() {
    if (!busy) onClose();
  }

  return (
    <Dialog
      open
      title={t("import.sources.remove.title")}
      onClose={close}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={busy}>
            {t("common.actions.cancel")}
          </Button>
          <Button variant="danger" onClick={() => void remove()} loading={busy}>
            {t("import.sources.remove.confirm")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm wrap-anywhere text-mist-300">
          {t("import.sources.remove.text", { name: source.name })}
        </p>
        {problem !== null && <FormMessage>{errorText(t, problem)}</FormMessage>}
      </div>
    </Dialog>
  );
}

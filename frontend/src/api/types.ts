/** Die Formen aus the design notes. Zeiten kommen als ISO-Text. */

/** Das eine Konto. */
export type Me = {
  username: string
  language: string
  created_at: string
}

export type SetupStatus = {
  setup_required: boolean
  version: string
}

/** Die Stufen, nach denen das Protokoll filtern kann. Eine Zeile kann auch CRITICAL tragen. */
export type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'

export type LogLine = {
  time: string
  level: string
  logger: string
  message: string
  request_id: string | null
}

export type LogModeName = 'quiet' | 'normal' | 'detailed' | 'trace'

export type LogMode = {
  mode: LogModeName
  /** Nur bei den tiefen Stufen: wann sie sich selbst abschalten. */
  until: string | null
  /** `NEXCRATE_LOG_LEVEL` ist gesetzt, dann laesst sich hier nichts umstellen. */
  fixed_by_env: boolean
}

/** 0 heisst ohne Frist, fuer `quiet` und `normal`. */
export type LogModeMinutes = 0 | 30 | 120 | 480

export type LogModeChange = {
  mode: LogModeName
  minutes: LogModeMinutes
}

export type LogsResponse = {
  lines: LogLine[]
  mode: LogMode
}

/** Die drei Medienarten, so wie der Server sie nennt. In Schritt 1 liefert er nur Filme. */
export type MediaKind = 'movie' | 'series' | 'album'

/** Zustand einer Fassung, Regeln unter "Library" im Plan. */
/** `incomplete` seit dem 19.09.2026: ein Album mit Dateien, dem Titel der Zielausgabe fehlen. */
export type VersionState = 'available' | 'incomplete' | 'upgrade' | 'downloading' | 'problem' | 'wanted' | 'unmonitored'

/**
 * Eine Fassung je Medienart, etwa "Full-HD" oder "4K". In Schritt 1 nur ein Name;
 * Qualitaetsregeln kommen spaeter mit eigenen Profilen.
 */
export type Version = {
  id: number
  kind: MediaKind
  label: string
  title_count: number
  /**
   * Seit Schritt 2b. Ein Server von davor schickt beides nicht; dann gilt "kein Profil".
   * `profile_line` ist null ohne Profil.
   */
  has_profile?: boolean
  profile_line?: ProfileLine | null
  /** Seit den benannten Profilen: auf welches die Fassung zeigt, und wie es heisst. */
  profile_id?: number | null
  profile?: string | null
  /**
   * Seit Schritt 3, nur bei Filmen: der Standardordner der Fassung, seit Befund 13 nur fuer neue Filme. Ein Server von
   * davor schickt ihn nicht; dann gilt "kein Ordner".
   */
  folder?: string | null
  /**
   * Die Verzoegerungsregel der Fassung, ab Werk wie in Radarr, Sonarr und Lidarr.
   * Ein Server von davor schickt sie nicht; dann gilt die Regel ab Werk.
   */
  delay?: DelayWithTags
  /** Wie viele Releases fuer diese Fassung gerade ihre Wartezeit absitzen. */
  waiting?: number
}

export type Protocol = 'usenet' | 'torrent'

/** Das Delay-Profil der Arr-Programme, hier je Fassung statt je Tag. Minuten zaehlen ab Erscheinen am Indexer. */
export type DelayRule = {
  enable_usenet: boolean
  enable_torrent: boolean
  preferred_protocol: Protocol
  usenet_minutes: number
  torrent_minutes: number
  bypass_highest_quality: boolean
  bypass_score: boolean
  minimum_score: number
}

/** Eine Regel fuer Titel mit einem dieser Tags, wie ein Delay-Profil mit Tags. */
export type TaggedDelayRule = DelayRule & { tags: string[] }

/** Die Regel der Fassung samt den Regeln fuer Tags, in ihrer Reihenfolge; ein Server von davor schickt keine. */
export type DelayWithTags = DelayRule & { tagged?: TaggedDelayRule[] }

/**
 * `GET /api/sources/{id}/delay`: das Delay-Profil ohne Tag einer Radarr-, Sonarr- oder Lidarr-Verbindung als Regel.
 * Gespeichert wird nichts. `tagged`: wie viele Profile dort an Tags haengen; `tagged_rules` sind sie als Regeln.
 */
export type SourceDelay = {
  app: SourceApp
  kind: MediaKind
  rule: DelayRule | null
  tagged: number
  shortened: boolean
  tagged_rules?: TaggedDelayRule[]
}

export const DEFAULT_DELAY: DelayRule = {
  enable_usenet: true,
  enable_torrent: true,
  preferred_protocol: 'usenet',
  usenet_minutes: 0,
  torrent_minutes: 0,
  bypass_highest_quality: true,
  bypass_score: false,
  minimum_score: 0,
}

/** `GET /api/waiting`: ein Release, das die Automatik behaelt statt es zu laden, bis die Wartezeit um ist. Nie ein Link. */
export type WaitingRelease = {
  id: number
  release_title: string
  indexer: string
  protocol: Protocol
  size_bytes: number | null
  quality: string | null
  score: number | null
  episodes: string[]
  origin: string
  published_at: string | null
  first_seen_at: string
  due_at: string
  /** Ist die Automatik dieser Art aus, laedt auch ein Release nicht, dessen Zeit um ist. */
  automatic_on?: boolean
  version_id: number
  version: string
  title: { id: number; title: string; year: number | null; kind: MediaKind }
}

export type WaitingPage = { items: WaitingRelease[]; total: number; page: number; per_page: number }

export type VersionCreate = { kind: MediaKind; label: string }

/** Umbenennen. Die Art bleibt, wie sie ist. */
export type VersionUpdate = { label: string }

/** Radarr fuellt eine Fassung fuer Filme, Sonarr (seit S1) eine fuer Serien, Lidarr (Musik M1) eine fuer Musik. */
export type SourceApp = 'radarr' | 'sonarr' | 'lidarr'

export type ImportStatus = 'running' | 'done' | 'failed'

export type ImportRun = {
  id: number
  source_id: number
  status: ImportStatus
  started_at: string
  finished_at: string | null
  titles_new: number
  titles_updated: number
  versions_total: number
  versions_removed: number
  /** Nur bei `failed`, ein Code aus errors.json. */
  error_code: string | null
  /** Die Werte zum Text des Codes, etwa `status` bei radarr_http_error. Sonst leer. */
  error_values: Record<string, string | number>
  /** Only for a Sonarr connection (S1): progress while running, the counts afterwards. An older server does not send it. */
  details?: ImportRunDetails | null
}

/** Eine Radarr-Instanz. Den Schluessel gibt der Server nie heraus, nur ob einer gespeichert ist. */
export type Source = {
  id: number
  name: string
  app: SourceApp
  url: string
  has_api_key: boolean
  /** Jede Quelle fuellt genau eine Fassung fuer Filme. */
  version_id: number
  last_import: ImportRun | null
  /**
   * Seit der Uebernahme: wann nexcrate die Fassungen dieser Verbindung zu eigenen gemacht
   * hat. Dann liest nexcrate dieses Radarr nicht mehr. Den Schluessel behaelt eine Uebernahme seit Befund 12 fuer
   * "Rückgängig machen" (`has_api_key` bleibt true); Uebernahmen von davor haben ihn geloescht. Ein Server von davor
   * schickt das Feld nicht.
   */
  taken_over_at?: string | null
}

export type SourceCreate = {
  name: string
  app: SourceApp
  url: string
  api_key: string
  version_id: number
}

/** Jedes Feld darf fehlen. Ohne `api_key` bleibt der gespeicherte Schluessel. */
export type SourceUpdate = Partial<{
  name: string
  url: string
  api_key: string
  version_id: number
}>

/**
 * Neu: Adresse und Schluessel aus dem Formular. Gespeichert: `source_id`, dazu
 * wahlweise eine geaenderte Adresse; ohne Schluessel nimmt der Server den gespeicherten.
 */
export type SourceTest = { url: string; api_key: string; app?: SourceApp } | { source_id: number; url?: string; api_key?: string }

export type SourceTestResult = {
  app: string
  app_version: string
  movie_count: number
  /** Sonarr: its series. null or missing for Radarr. */
  series_count?: number | null
}

export type LibrarySort = 'title' | 'year' | 'added'

export type TitleVersionSummary = {
  id: number
  label: string
  state: VersionState
  /** 0 bis 100, nur beim Laden. */
  progress: number | null
  /** Radarrs Einordnung der Datei, etwa "Bluray-1080p". null ohne Datei. */
  quality: string | null
  /** null ohne Datei. */
  size_bytes: number | null
  /** A series version's episode counts (S1). null for a movie version; an older server does not send it. */
  counts?: EpisodeCounts | null
  /** Seit S6: Dateien der Serienfassung ohne Folge, nicht ausgelassen. 0 bei Filmen; ein aelterer Server schickt es nicht. */
  unclear_files?: number
  /** Ob nexcrate diese Fassung beobachtet (Rueckmeldung 20.09.2026). Ein aelterer Server schickt es nicht. */
  monitored?: boolean
}

export type TitleSummary = {
  id: number
  /** Seit T1: die Tags, bei einem Album die seines Kuenstlers. */
  tags?: string[]
  kind: MediaKind
  title: string
  year: number | null
  /** `/api/images/{id}/poster?v=...` oder null. */
  poster_url: string | null
  versions: TitleVersionSummary[]
  /** Nur bei einem Album (Musik M1): der Name seines Kuenstlers. */
  artist?: string | null
  /** V4: IMDbs Wertung aus nexcrates Kopie der taeglichen Datei; null ohne. */
  imdb_rating?: number | null
}

export type LibraryPage = {
  items: TitleSummary[]
  total: number
}

export type LibraryStats = {
  movies: number
  series: number
  /** Alle Folgen aller Serien; seit der Rueckmeldung vom 20.09.2026 steht sie neben den Serien. */
  episodes?: number
  /** Alle Kuenstler, auch die ohne Album in nexcrate. */
  artists?: number
  albums: number
  size_bytes: number
  problems: number
  /** Titel mit mindestens einer Fassung im Zustand problem. So viele zeigt der Filter. */
  problem_titles: number
  /** Seit S6: Serien mit unklaren Dateien. So viele zeigt der Filter "Unklare Dateien". */
  unclear_titles?: number
  /** Musik M6: Alben mit unklaren Dateien. */
  unclear_albums?: number
  /** Seit dem Musik-Abschluss: je Art und Zustand die Titel, die der Filter zeigt. Zustaende ohne Titel fehlen. */
  state_titles?: Partial<Record<MediaKind, Partial<Record<VersionState, number>>>>
}

/** Wer eine Fassung an den Titel gebracht hat: ein Import aus Radarr oder du selbst. */
export type VersionAddedBy = 'import' | 'owner'

export type TitleVersion = {
  /** /api/v1 V2: the reference of the program that asked for this version, and the name of its key. An older server lacks both. */
  origin?: string | null
  origin_key?: string | null
  /** Die Nummer dieser Fassung am Titel, nicht die der Fassung unter Einstellungen. */
  id: number
  /**
   * Die Nummer der Fassung unter Einstellungen, wie sie `PATCH /library/{id}/versions`
   * erwartet. ⚠️ Steht nicht im Plan; fehlt sie, gleicht die Seite ueber den Namen ab,
   * der je Art nur einmal vorkommt.
   */
  version_id?: number
  label: string
  added_by: VersionAddedBy
  profile_name: string | null
  root_folder: string | null
  state: VersionState
  quality: string | null
  upgrade_to: string | null
  /**
   * Since decision 20: the qualities of `upgrade_to` when it is a group, for a version of nexcrate's own, those that reach
   * the profile's target resolution. Empty for a single quality and for a version a source feeds. An older server does not
   * send it.
   */
  upgrade_to_items?: string[]
  /**
   * Since decision 20: why nexcrate would still upgrade the file of a version of its own. quality: below the cutoff or the
   * target resolution; score: at the cutoff quality with a score below `upgrade_until`. null for a version a source feeds
   * and while the file is done.
   */
  upgrade_reason?: 'quality' | 'score' | null
  /** Since decision 20: the score of the file of a version of nexcrate's own, as the upgrade check of a search scores it. */
  current_score?: number | null
  /** Since decision 20: the profile's upgrade-until score; null while its upgrades are switched off. */
  upgrade_until?: number | null
  size_bytes: number | null
  languages: string[]
  release_group: string | null
  relative_path: string | null
  source_name: string | null
  /** Ob nexcrate diese Fassung beobachtet. Aus: Es sucht nichts mehr dafuer. Ein aelterer Server schickt es nicht. */
  monitored?: boolean
  /** Der letzte fehlgeschlagene Download dieser Fassung, den du noch nicht erledigt hast. */
  last_failure?: { id: number; at: string; reason: string } | null
  /** Releases, die fuer diese Fassung ihre Wartezeit absitzen: wie viele, und ab wann das erste laden darf. */
  waiting?: { count: number; due_at: string; due?: boolean } | null
  progress: number | null
  problem_code: string | null
  /** Seit Schritt 3: der aktive Download, den nexcrate selbst fuer die Fassung laedt. Ein Server von davor schickt ihn nicht. */
  download?: TitleDownload | null
  /**
   * Seit Schritt 3 (Aenderung B): wie viele Downloads dieser Fassung noch im Download-Programm sind und nicht abgelegt,
   * Hinweise wie `dangerous_file` eingeschlossen. Genau die entfernt `remove_downloads` mit.
   */
  pending_downloads?: number
  /**
   * Seit Befund 13: wo die Datei liegt (`file`), wohin die naechste kommt (`target`) oder wo
   * Radarr den Film fuehrt (`radarr`, der Pfad wie Radarr ihn sieht). null ohne Angabe. Ein Server von davor schickt
   * das Feld nicht.
   */
  location?: VersionLocation | null
  /**
   * Seit Schritt 3c (C9): die Untertitel-Dateien, die nexcrate neben die Datei dieser Fassung gelegt hat. Ein Server
   * von davor schickt das Feld nicht; dann gibt es keine.
   */
  subtitles?: Subtitle[]
  /**
   * Since the library-from-disk block: the state of the version's `release.nex`, or
   * null for a version a source feeds or one never looked at. An older server does not send it.
   */
  companion?: VersionCompanion | null
  /** Where the stored quality came from: the name, the media data or Radarr. An older server does not send it. */
  quality_from?: QualityFrom | null
  /** The stored media data of the file (L7), or null. An older server does not send it. */
  media?: MediaInfo | null
  /** Series (S1): what the version watches. null for a movie. */
  watch?: SeriesWatch | null
  /** Series (S1): the episode counts. null for a movie. */
  counts?: EpisodeCounts | null
  /** Seit S4: Folgen, die nur Sonarr kennt, meist Specials. Leer bei Filmen und eigenen Fassungen. */
  source_only_episodes?: SourceOnlyEpisode[]
}

/** Eine Folge, die nur die Sonarr-Verbindung kennt (S4, Entscheidung 47). */
export type SourceOnlyEpisode = { season: number; episode: number; name: string; air_date: string | null; watched: boolean; has_file: boolean }

/** Die Arten von `TitleVersion.location`. `sonarr` seit S1: der Serienordner, wie Sonarr ihn sieht. */
export type VersionLocationKind = 'file' | 'target' | 'radarr' | 'sonarr'

export type VersionLocation = { kind: VersionLocationKind; path: string }

/**
 * Seit Schritt 3 kommen `grabbed` (zum Laden uebergeben) und `failed` dazu, mit der Uebernahme `taken_over` und
 * `takeover_undone` (Detail bei beiden: Name der Verbindung).
 */
export type HistoryEvent =
  | 'added'
  | 'relocated'
  | 'imported'
  | 'grabbed'
  | 'failed'
  | 'taken_over'
  | 'takeover_undone'
  | 'found_on_disk'
  | 'restored'
  /** Series (S1): TMDB renumbered an episode; detail `S01E05 S01E06`. */
  | 'renumbered'
  /** Series (S1): TMDB added episodes long after they aired; detail the count. */
  | 'episodes_late'
  /** Series (S4): a download filed episodes; detail `filed=7 skipped=1 missing=S02E07,S02E08 not_filed=S02E09`. */
  | 'episodes_filed'
  /** Series (S4): a file named with TBA got TMDB's title; detail the codes, `S01E05,S01E06`. */
  | 'episode_renamed'
  /** Series (S6): the repair of a wrong bridge to TVDB moved a taken-over file; detail `S03E05 S03E04`, `-` for none. */
  | 'file_relinked'
  /** Music (M1): a release group appeared at the first refresh after it was added; detail its type, e.g. `Album+Live`. */
  | 'album_appeared'
  /** Music (M1): MusicBrainz changed the type; detail `old type > new type`. Watching does not change. */
  | 'album_type_changed'
  /** Music (M1): MusicBrainz no longer lists the release group or release; no detail. */
  | 'album_gone'
  /** Music (M1): the target release changed, by the rule or the owner; detail `old release text > new release text`. */
  | 'target_changed'
  /** Music (M4): an album download filed files; detail `filed=14 missing=0 release=7 download=4`, `download_id` set. */
  | 'album_filed'
  /** Music (M4): the owner wrote the tags of an album again. */
  | 'tags_written'
  /** /api/v1 V2: a program asked for the version or took it back; detail the key's name, after a line break its origin. */
  | 'requested'
  | 'withdrawn'
  /** V2: files into the recycle bin, and one back; detail the count, after a space the key's name when a program did it. */
  | 'files_deleted'
  | 'file_restored'
  /** /api/v1 V3: a program acted on a download; detail the action, after a space the key's name. */
  | 'operated'

export type HistoryEntry = {
  at: string
  event: HistoryEvent
  /** Label der Fassung. */
  version: string
  detail: string | null
  /** Bei `album_filed` der Download; `GET /api/downloads/{id}/album-files` sagt, welche Datei auf welchen Titel kam. */
  download_id?: number | null
}

export type TitleDetail = Omit<TitleSummary, 'versions'> & {
  original_title: string | null
  runtime_min: number | null
  /** Englisch, wie die Quelle sie nennt. */
  genres: string[]
  overview: string | null
  tmdb_id: number | null
  imdb_id: string | null
  history: HistoryEntry[]
  versions: TitleVersion[]
  /** Seit Schritt 3c (C8): wann nexcrate den Titel von selbst sucht und was die letzte Suche fand. Ein Server von davor schickt es nicht. */
  search_plan?: SearchPlan | null
  /** Only for a series (S1): seasons with counts per version, numbering, late episodes, unassigned files. */
  series?: SeriesBlock | null
  /** Only for an album (Musik M1): the credit, the target release with its reasons, its tracks, the releases. */
  album?: AlbumBlock | null
}

/* ------------------------------------------------------------------------------------------ */
/* Schritt 2a, wie in the design notes. ⚠️ Alle Formen der Schnittstelle stehen hier an   */
/* einer Stelle: Aendert der Server nach einer Messung ein Feld, wird es nur hier nachgezogen.  */
/* ------------------------------------------------------------------------------------------ */

/** `GET /api/health`, ohne Anmeldung. */
export type Health = {
  status: string
  version: string
  /** Wann der laufende Prozess startete. Nach dem Einspielen wartet die Seite, bis sich das aendert. */
  started: string
}

/** `GET /api/tmdb`. Den Token selbst gibt der Server nie heraus, nur ob einer gespeichert ist. */
export type TmdbState = {
  configured: boolean
  /** Wann der Token zuletzt bei TMDB geprueft wurde. */
  checked_at: string | null
}

export type TmdbResult = {
  tmdb_id: number
  title: string
  original_title: string | null
  year: number | null
  overview: string | null
  /** `/api/tmdb/poster/w185/...` vom eigenen Server, oder null. */
  poster_url: string | null
  /** Gesetzt, wenn der Film schon in der Bibliothek steht. */
  title_id: number | null
  /** Die Fassungen (Nummern unter Einstellungen), die der Titel schon hat. Leer, wenn er fehlt. */
  version_ids: number[]
  /** Since L8 of the library-from-disk block: stored scan rows that carry this TMDB number. An older server does not send it. */
  on_disk?: OnDiskFolder[]
}

export type TmdbSearch = {
  page: number
  total_pages: number
  total: number
  results: TmdbResult[]
}

/** `POST /api/library` fuer einen Film. Mindestens eine Fassung, sonst 422 `invalid_input`. */
export type MovieAdd = {
  kind: 'movie'
  tmdb_id: number
  version_ids: number[]
}

/** `POST /api/library` and `POST /api/library/preview` for a series (S1): every version with its rule. */
export type SeriesAdd = {
  kind: 'series'
  tmdb_id: number
  versions: SeriesVersionChoice[]
  /** null takes TMDB's proposal. */
  series_type?: SeriesType | null
}

export type LibraryAdd = MovieAdd | SeriesAdd

/** `PATCH /api/library/{id}/versions`, beides mit Nummern der Fassungen unter Einstellungen. */
export type TitleVersionChange = {
  add: number[]
  remove: number[]
  /** Seit Schritt 3: laufende Downloads der entfernten Fassungen auch im Download-Programm entfernen. Ohne: 409 `version_download_active`. */
  remove_downloads?: boolean
}

export type IndexerKind = 'newznab' | 'torznab'

export type IndexerCategory = {
  id: number
  name: string
  subcats?: IndexerCategory[]
}

/** Was ein Indexer ueber sich sagt (`t=caps`). */
export type IndexerCaps = {
  movie_search: boolean
  movie_params: string[]
  search_params: string[]
  search_engine: string | null
  limit_max: number | null
  limit_default: number | null
  categories: IndexerCategory[]
  /** Seit S3: ob der Indexer `t=tvsearch` kann, und mit welchen Parametern. Ein Server von davor schickt beides nicht. */
  tv_search?: boolean
  tv_params?: string[]
}

/** Den Schluessel gibt der Server nie heraus, nur `has_api_key`. */
export type Indexer = {
  id: number
  name: string
  kind: IndexerKind
  url: string
  has_api_key: boolean
  categories: number[]
  /** Seit S3: die Kategorien fuer Serien, eigene Liste neben der fuer Filme. Leer heisst: fuer Serien uebersprungen. */
  series_categories?: number[]
  /** Seit M3: die Kategorien fuer Alben, ohne eigene Wahl die Vorgabe aus den caps. Leer heisst: fuer Alben uebersprungen. */
  music_categories?: number[]
  /** Seit M3: false, solange die Musik-Kategorien der Vorgabe aus den caps folgen. */
  music_categories_chosen?: boolean
  /** Seit Anime A3: die Kategorien, die eine Anime-Serie zusaetzlich fragt; ohne eigene Wahl die Vorgabe aus den caps. */
  anime_categories?: number[]
  /** Seit Anime A3: false, solange die Anime-Kategorien der Vorgabe aus den caps folgen. */
  anime_categories_chosen?: boolean
  /** B3: ob eine Anime-Serie auch im Standardformat (S01E01) gesucht wird, nicht nur ueber die Durchzaehlung. */
  anime_standard_format_search?: boolean
  enabled: boolean
  /** null: Der Indexer hat keine lesbaren caps geschickt, es gelten Standardwerte. */
  caps: IndexerCaps | null
  caps_checked_at: string | null
  paused_until: string | null
  last_error_code: string | null
  from_source: { source_id: number; name: string } | null
  /*
   * Seit Schritt 2c, siehe `IndexerSearchSettings`. Ein Server von davor schickt sie nicht; dann
   * gelten die Standardwerte.
   */
  priority?: number
  minimum_seeders?: number | null
  multi_languages?: string[]
  remove_year?: boolean
  /*
   * Seit Schritt 3c, siehe the design notes unter "API". Ein Server von davor schickt sie nicht; dann gibt es
   * kein Tageslimit, keine gemeldete Nutzung, keine Pause der Automatik und kein Lesen neuer Releases.
   */
  /** 1 bis 100000, selbst eingetragen. null: keins. */
  daily_limit?: number | null
  /** Was der Indexer ueber `newznab:apilimits` zuletzt gemeldet hat, oder null. */
  usage?: IndexerUsage | null
  escalation_level?: number
  /** Nach Fehlern haelt nexcrate nur die Automatik bis dahin an; eigene Suchen gehen weiter. */
  automatic_paused_until?: string | null
  rss?: IndexerRss | null
  /** Seit T2: mit Tags nur fuer Titel mit einem davon. Ein Server von davor schickt keine. */
  tags?: string[]
}

export type IndexerCreate = {
  name: string
  kind: IndexerKind
  url: string
  /** Darf leer sein, manche Indexer brauchen keinen. */
  api_key: string
  categories: number[]
  /** Seit S3. Fehlt es beim Anlegen, nimmt der Server die Vorgabe aus den caps. */
  series_categories?: number[]
  /** Seit M3. Fehlt es, folgen die Musik-Kategorien der Vorgabe aus den caps. */
  music_categories?: number[]
  /** Nur beim Aendern: zurueck auf die Vorgabe aus den caps. */
  music_categories_default?: boolean
  /** Seit Anime A3. Fehlt es, folgen die Anime-Kategorien der Vorgabe aus den caps. Leer: nur die Serienkategorien. */
  anime_categories?: number[]
  /** Nur beim Aendern: zurueck auf die Vorgabe aus den caps. */
  anime_categories_default?: boolean
  /** B3: weggelassen bleibt es, wie es ist. */
  anime_standard_format_search?: boolean
  enabled: boolean
  /** Speichern, obwohl der Indexer in den Kategorien nichts liefert. Ohne: 409 `indexer_categories_empty`. */
  confirm_empty?: boolean
  /** Anime B4: speichern, obwohl eine Anfrage in den gewaehlten Anime-Kategorien nichts fand. */
  confirm_anime_empty?: boolean
  /** Seit Schritt 3c: 1 bis 100000, null heisst keins. Sonst 422 `invalid_input`. */
  daily_limit?: number | null
  /** Seit T2: alle Tags, nach Namen. */
  tags?: string[]
} & Partial<IndexerSearchSettings>

/** Jedes Feld darf fehlen. Ohne `api_key` bleibt der gespeicherte Schluessel. Ohne `kind`: Die Art aendert sich nie (422 `indexer_kind_locked`). */
export type IndexerUpdate = Partial<Omit<IndexerCreate, 'kind'>>

/**
 * Neu: Art, Adresse und Schluessel aus dem Formular. Gespeichert: `indexer_id`, dazu wahlweise
 * Neues. `categories` fehlt beim ersten Test eines neuen Indexers; dann prueft der Server die
 * ueblichen Filmkategorien.
 */
export type IndexerTest =
  | { kind: IndexerKind; url: string; api_key: string; categories?: number[]; series_categories?: number[]; music_categories?: number[] }
  | { indexer_id: number; kind?: IndexerKind; url?: string; api_key?: string; categories?: number[]; series_categories?: number[]; music_categories?: number[] }

export type IndexerTestResult = {
  caps: IndexerCaps | null
  /** 1, wenn der Feed in den Kategorien etwas liefert, sonst 0. Wie Radarr prueft nexcrate nur, ob er leer ist. */
  feed_items: number
  /**
   * Die Kategorien, die der Server ohne eigene Wahl nimmt: aus caps 2000 bis 2999, sonst die ueblichen.
   * Steht nicht im Plan, der Server schickt es. Fehlt es, rechnet die Seite dasselbe selbst aus.
   */
  default_categories?: number[]
  /** Seit S3, nur mit `series_categories` im Test: 1, wenn der Feed in den Serienkategorien etwas liefert, sonst 0. */
  series_feed_items?: number | null
  /** Seit S3: die Serienkategorien, die der Server ohne eigene Wahl nimmt. */
  default_series_categories?: number[]
  /** Seit M3: die Musik-Kategorien, die der Server ohne eigene Wahl nimmt. */
  default_music_categories?: number[]
  /** Seit Anime A3: 5070 und seine Unterkategorien aus den caps; ohne caps 5070; ohne 5070 in den caps keine. */
  default_anime_categories?: number[]
  /** Nur mit `music_categories` im Test (Indexer aus Lidarr): 1, wenn der Feed in den Musik-Kategorien etwas liefert. */
  music_feed_items?: number | null
  /** Die Adresse, die geprueft wurde. Ohne Pfad haengt der Server /api an. */
  url?: string
}

export type IndexerRelease = {
  title: string
  size_bytes: number | null
  published_at: string | null
  categories: number[]
  seeders: number | null
  peers: number | null
  grabs: number | null
}

/** `POST /api/indexers/{id}/search`: eine Anfrage mit `limit=50`, ohne weitere Seiten. */
export type IndexerSearch = {
  total: number
  took_ms: number
  results: IndexerRelease[]
}

/**
 * Ein Indexer, wie Radarr ihn kennt (`GET /api/sources/{id}/indexers`). Den Schluessel
 * schickt Radarr nur als `********`, deshalb fehlt er hier ganz.
 */
export type RadarrIndexer = {
  radarr_indexer_id: number
  name: string
  kind: IndexerKind
  url: string
  categories: number[]
  /** true, sobald Radarr den Indexer fuer irgendetwas nutzt. */
  enabled: boolean
  already_added: boolean
}

export type IndexerFromSource = {
  source_id: number
  radarr_indexer_id: number
  api_key: string
}

/* ------------------------------------------------------------------------------------------ */
/* Schritt 2b, wie in the design notes: Profile, Fragen, Release-Pruefer, TRaSH-Stand.    */
/* ------------------------------------------------------------------------------------------ */

/** Pflicht oder gern dazu. Die Rollen schickt der Server mit der Frage `languages`. */
export type LanguageRole = 'required' | 'preferred'

export type LanguageEntry = { code: string; role: LanguageRole }

/** Was eine Antwort sein kann: Auswahl, Ja oder Nein, Zahl oder keine, Sprachliste. */
export type AnswerValue = string | number | boolean | null | LanguageEntry[]

/** Die drei einfachen Formen einer Bedingung. */
export type SimpleCondition =
  | { question: string; is: AnswerValue }
  | { question: string; includes: string }
  | { question: string; required_at_least: number }

/**
 * Oder: gilt, wenn jede Bedingung mindestens einer inneren Liste gilt. Innen stehen nur die
 * einfachen Formen, nichts verschachtelt. Seit den Aenderungen nach dem Test von 2b (13.09.2026).
 */
export type AnyCondition = { any: SimpleCondition[][] }

/**
 * Eine Bedingung: eine der einfachen Formen oder `any`, und keine anderen. Alle Bedingungen einer
 * Frage muessen gelten; eine leere Liste heisst immer.
 */
export type QuestionCondition = SimpleCondition | AnyCondition

type QuestionBase = {
  id: string
  /** Nur im ausfuehrlichen Modus (`mode` ist `detailed`). */
  detailed: boolean
  when: QuestionCondition[]
}

export type ChoiceQuestion = QuestionBase & { type: 'choice'; options: string[]; default: string }
export type BooleanQuestion = QuestionBase & { type: 'boolean'; default: boolean }
export type LanguagesQuestion = QuestionBase & { type: 'languages'; codes: string[]; roles: LanguageRole[]; default: LanguageEntry[] }
export type NumberQuestion = QuestionBase & { type: 'number'; min: number; max: number; nullable: boolean; default: number | null }

export type Question = ChoiceQuestion | BooleanQuestion | LanguagesQuestion | NumberQuestion

/** `GET /api/profiles/questions?kind=movie`. Die Texte stehen in der Oberflaeche, je Frage und Wert. */
export type QuestionList = {
  kind: MediaKind
  schema: number
  questions: Question[]
}

/** Die Antworten eines Profils: die Fragen nach Kennung, dazu `schema` und `kind`. */
export type ProfileAnswers = {
  schema: number
  kind: MediaKind
  [id: string]: AnswerValue
}

/** Ein Baustein der Regeln mit seinen Punkten, etwa "German DL" mit 11000. */
export type ScoredFormat = { name: string; score: number }

/** Eine erlaubte Qualitaet oder eine Gruppe gleichwertiger. */
export type SummaryQuality = string | { group: string; items: string[] }

export type SummarySize = {
  quality: string
  min_gb_per_hour: number | null
  /** null heisst ohne Obergrenze. */
  max_gb_per_hour: number | null
}

/** Ein Hinweis zum Profil als Code mit Werten, etwa `size_limit_below_minimum {qualities}`. */
export type ProfileWarning = { code: string; [value: string]: unknown }

export type ProfileSummary = {
  /** Von der niedrigsten zur hoechsten. */
  qualities: SummaryQuality[]
  cutoff: string
  min_score: number
  upgrade_until: number
  /** Hoechstens zehn, die hoechsten Punkte zuerst. */
  preferred: ScoredFormat[]
  /** Hoechstens zehn, die niedrigsten Punkte zuerst. */
  avoided: ScoredFormat[]
  formats_total: number
  sizes: SummarySize[]
  max_gb_per_hour: number | null
  languages: LanguageEntry[]
  required_languages: string
  warnings: ProfileWarning[]
}

export type Profile = {
  /** Null, wenn das Profil fuer sich gelesen wurde. */
  version_id: number | null
  kind: MediaKind
  answers: ProfileAnswers
  /** Null, solange das Profil leer ist. */
  summary: ProfileSummary | null
  /** Nichts hat es bisher gefuellt: keine Regeln, es urteilt ueber nichts. */
  empty?: boolean
  trash_commit: string
  /** Gebaut mit einem aelteren TRaSH-Stand, bis es neu gespeichert wird. */
  outdated: boolean
  /** `wizard`, oder `expert`, solange der Besitzer das Profil von Hand haelt. */
  mode: string
  /** Das Profil selbst; mehrere Fassungen duerfen auf dasselbe zeigen. */
  profile_id: number
  /** Wie es heisst. */
  name: string
  updated_at: string
}

/** Ein Profil in der Liste: Name, Art, wer danach urteilt. */
export type ProfileBrief = {
  id: number
  name: string
  kind: MediaKind
  mode: string
  /** Nichts hat es bisher gefuellt. */
  empty?: boolean
  outdated: boolean
  /** Die Namen der Fassungen, die danach urteilen. */
  used_by: string[]
  updated_at: string
}

export type ProfilePreviewRequest = { version_id: number; answers: ProfileAnswers }

/** `POST /api/profiles/preview`: die Antworten, wie der Server sie speichern wuerde, und die Zusammenfassung. */
export type ProfilePreview = { answers: ProfileAnswers; summary: ProfileSummary }

/** ⚠️ `kind` statt `version_id`, wenn die Datei fuer ein Profil ohne Fassung gelesen wird. */
export type ProfileImportRequest = { yaml: string; version_id?: number; kind?: MediaKind }

/** `POST /api/profiles/import`. Gespeichert wird danach ueber PUT. */
export type ProfileImport = {
  kind: MediaKind
  /** Der Name der Fassung, aus der die Datei stammt. */
  name: string
  answers: ProfileAnswers
  summary: ProfileSummary
  trash_commit: string
  commit_differs: boolean
}

/** Die Kurzform eines Profils fuer die Zeile unter der Fassung. */
export type ProfileLine = {
  resolution: string
  source: string
  languages: LanguageEntry[]
  /** null, wenn die Frage nicht gestellt wurde, etwa bei 1080p. */
  hdr: string | null
  max_gb_per_hour: number | null
  outdated: boolean
}

export type ReleaseCheckRequest = {
  name: string
  title_id?: number
  runtime_min?: number
  size_bytes?: number
}

export type ParsedRelease = {
  title: string | null
  year: number | null
  group: string | null
  source: string | null
  resolution: number | null
  modifier: string | null
  quality: string | null
  revision: { version: number; real: number; repack: boolean } | null
  edition: string | null
  languages: string[]
  hardcoded_subs: string | null
}

/** Ein Grund, warum ein Release nicht passt. Code mit Werten, nie ein Satz. */
export type ReleaseRejection = { code: string; [value: string]: unknown }

export type ReleaseUpgrade = {
  current_quality: string | null
  current_score: number
  better: boolean
  /** null, wenn `better` true ist. */
  reason: string | null
}

/** Das Ergebnis der Bewertung ohne `parsed`. */
export type ReleaseResult = {
  accepted: boolean
  score: number
  matched: ScoredFormat[]
  rejections: ReleaseRejection[]
  /** null ohne vorhandene Datei. */
  upgrade: ReleaseUpgrade | null
  /**
   * Seit dem Test von 2c (14.09.2026): Das Release passt, liegt aber unter der Zielaufloesung des
   * Profils, etwa 1080p fuer eine 4K-Fassung, die erst nimmt, was da ist. Die Seite zeigt dann
   * "Vorerst" statt "Passt". Ein Server von davor schickt es nicht; dann gilt false.
   */
  below_target?: boolean
}

export type ReleaseCheckVersion = {
  version_id: number
  label: string
  has_profile: boolean
  /** null ohne Profil. */
  result: ReleaseResult | null
}

export type ReleaseCheck = {
  parsed: ParsedRelease
  versions: ReleaseCheckVersion[]
}

/* ---------------------------------------------------------------------------------------- */
/* Serien S2, wie in the design notes: der Release-Pruefer je Serienfassung.          */
/* ---------------------------------------------------------------------------------------- */

/** Hoechstens so viele Namen nimmt der Pruefer auf einmal. */
export type SeriesCheckRequest = {
  names: string[]
  title_id?: number
  version_ids?: number[]
  runtime_min?: number
  /** Nur bei genau einem Namen. */
  size_bytes?: number
}

/** Die Form, die nexcrate im Namen liest. `refused` traegt den Code einer Form, die es nicht nimmt. */
export type ParsedSeriesRelease = {
  series_title: string | null
  year: number | null
  form: string
  /** `single_episode`, `multi_episode` oder `season_pack`; null bei einer abgelehnten Form. */
  release_type: string | null
  season: number | null
  seasons: number[]
  episodes: number[]
  air_date: string | null
  part: number | null
  absolute: number[]
  refused: string | null
  group: string | null
  source: string | null
  resolution: number | null
  quality: string | null
  revision: { version: number; real: number; repack: boolean } | null
  languages: string[]
  hardcoded_subs: string | null
}

/** Eine getroffene Folge: die Nummer, unter der sie auf der Seite der Serie steht, und worueber sie getroffen wurde. */
export type SeriesMatchEpisode = { episode_id: number; code: string; name: string; via: string }

/** Ueber welches Schema die Folgen gefunden wurden, und was daran unsicher ist. */
export type SeriesMatch = {
  /** `scene`, `tvdb`, `tmdb`, `air_date` oder null. */
  via: string | null
  ambiguous: boolean
  /** Die andere Lesart: ihr Schema und die Nummern ihrer Folgen. */
  other: { via?: string; codes?: string[] } | null
  missing: number[]
  episodes: SeriesMatchEpisode[]
  /** `unverified_scene`, `two_dates`, `not_daily`; seit dem Durchlauf ab null `group_counting` (die Gruppe zaehlt nach `via`); seit Anime A2 `not_anime`. */
  notes: string[]
}

/** Der Zustand einer Folge, wenn nexcrate das Release naehme. */
export type SeriesEpisodeState = 'fills' | 'replaces' | 'keeps' | 'not_watched'

export type SeriesEpisodeResult = {
  episode_id: number
  code: string
  state: SeriesEpisodeState
  upgrade?: ReleaseUpgrade | null
}

/** Ein Hinweis zum Ergebnis, der nichts ablehnt: `runtime_unknown`, `title_mismatch`. */
export type SeriesNote = { code: string; [value: string]: unknown }

export type SeriesReleaseResult = {
  accepted: boolean
  score: number
  matched: ScoredFormat[]
  rejections: ReleaseRejection[]
  notes: SeriesNote[]
  below_target: boolean
  /** Das Release liegt unter dem Ziel und steht mit ihm in einer Gruppe, wo nur die Punkte entscheiden. */
  below_target_in_group: boolean
  episodes: SeriesEpisodeResult[]
  /** null ohne Serie. */
  would_take: boolean | null
  replaces: { files: number; size_bytes: number }
  runtime_min: number | null
  /** Der Platz unter den eingegebenen Namen, 1 zuerst. */
  rank: number | null
}

export type SeriesCheckVersion = {
  version_id: number
  label: string
  has_profile: boolean
  result: SeriesReleaseResult | null
}

export type SeriesCheckRelease = {
  name: string
  parsed: ParsedSeriesRelease
  /** null ohne Serie. */
  match: SeriesMatch | null
  versions: SeriesCheckVersion[]
}

export type SeriesCheck = { releases: SeriesCheckRelease[] }

/* ------------------------------------------------------------------------------------------ */
/* Schritt 2c, wie in the design notes: Suche je Film und die Indexer-Einstellungen dazu. */
/* ------------------------------------------------------------------------------------------ */

/** Was an einem Indexer die Suche veraendert. `PATCH /api/indexers/{id}` nimmt es an. */
export type IndexerSearchSettings = {
  /** 1 bis 50, ueblich 25. Bei sonst gleichen Releases gewinnt die kleinere Zahl. */
  priority: number
  /** Nur Torznab, dort ueblich 1. Bei Newznab null. */
  minimum_seeders: number | null
  /** ISO 639-1. Ein Release mit MULTi im Namen bekommt diese Sprachen dazu. */
  multi_languages: string[]
  /** Die Suche nach dem Titel laesst die Jahreszahl weg. */
  remove_year: boolean
}

export type SearchState = 'running' | 'done'

/** `skipped` seit S3: Eine Suche nach einer Serie hat den Indexer uebersprungen, etwa ohne Serienkategorien. */
export type SearchIndexerState = 'waiting' | 'searching' | 'done' | 'failed' | 'timeout' | 'skipped'

/** Eine Anfrage an einen Indexer. `text` ist der Suchtext oder `ids`, nie ein Schluessel. */
export type SearchQuery = {
  kind: 'id' | 'title'
  text: string
  releases: number
}

export type SearchIndexer = {
  indexer_id: number
  name: string
  state: SearchIndexerState
  /** Ein Indexer-Code wie in 2a, etwa `indexer_key_rejected`. null ohne Fehler. */
  error_code: string | null
  queries: SearchQuery[]
  releases: number
  /** null, solange der Indexer nicht fertig ist. */
  took_ms: number | null
}

/** Ein Grund, warum nichts passt, und bei wie vielen Releases er zutraf. */
export type ReasonCount = { code: string; count: number }

export type SearchVersion = {
  version_id: number
  label: string
  has_profile: boolean
  /** `release_key` des Releases, das nexcrate nehmen wuerde. null, wenn keines in Frage kommt. */
  would_take: string | null
  /** Nichts kommt in Frage, und die Fassung hat eine Datei: Die bleibt. */
  keeps_current: boolean
  /** Die haeufigsten Ablehnungen, wenn nichts passt. */
  nothing_fits: ReasonCount[]
  /**
   * Seit Schritt 3: warum die Fassung unabhaengig vom Release nicht laden kann, ein Code aus `LoadBlock`
   * ohne `no_client_for_protocol`. null, wenn nichts dagegen spricht. Ein Server von davor schickt es nicht.
   */
  load_block?: string | null
  /** Seit S4, Serien: ob jedes Release von `takes` laden kann, fuer "Alles laden". */
  takes_can_load?: boolean
  /* Seit S3, nur bei Serien gefuellt. Ein Film und ein Server von davor schicken sie leer oder gar nicht. */
  /** Der Name der Verbindung, die die Fassung fuellt, etwa ein Sonarr. */
  fed_by?: string | null
  /** Was nexcrate nehmen wuerde, in dieser Reihenfolge. `would_take` bleibt bei Serien null. */
  takes?: SeriesTake[]
  /** Ueberwachte, gelaufene Folgen ohne Datei, die kein Release der Antwort nennt. */
  not_found?: string[]
  /** Seit dem Durchlauf ab null: solche Folgen, zu denen es Releases gibt, von denen keines passt; die Gruende stehen in `nothing_fits`. */
  no_fit?: string[]
  /** Seit S4: Staffelpakete, die weniger als die Haelfte ihrer Folgen braechten; gleich gute kleinere Releases nehmen ihren Platz. */
  packs_left_out?: PackLeftOut[]
}

/** Ein ausgelassenes Staffelpaket: wie viele Folgen der Suche es braechte, von wie vielen, die es enthaelt. */
export type PackLeftOut = { release_key: string; brings: number; episodes: number }

/** Ein Release der Zusammenstellung "würde nehmen" mit den Nummern der Folgen, wie die Seite der Serie sie zeigt. */
export type SeriesTake = {
  release_key: string
  fills: string[]
  replaces: string[]
  /** Folgen im Release, die ein frueheres Release der Zusammenstellung schon abdeckt. */
  covered_elsewhere: string[]
}

/** Die Folgen, die ein Release in einer Suche meint. `episodes` sind die Nummern wie auf der Seite der Serie. */
export type SearchMatch = Pick<SeriesMatch, 'via' | 'ambiguous' | 'other' | 'missing' | 'notes'> & { episodes: string[] }

/** Wofuer eine Suche nach einer Serie laeuft. `code` ist `S02` oder die Nummer der Folge. */
export type SearchScope = {
  kind: 'series' | 'season' | 'episode'
  season?: number | null
  episode_id?: number | null
  code?: string | null
}

/** Der Inhalt von `POST /api/library/{id}/search` fuer eine Serie. Ein Film schickt keinen. */
export type SearchStartBody = { scope: 'series' } | { scope: 'season'; season: number } | { scope: 'episode'; episode_id: number }

export type SearchReleaseVersion = {
  version_id: number
  /** Wie beim Release-Pruefer, dazu `not_enough_seeders` unter den Ablehnungen. null ohne Profil und bei Serien. */
  result: ReleaseResult | null
  /** Seit S3, bei Serien: das Ergebnis des Serien-Pruefers. */
  series_result?: SeriesReleaseResult | null
  /** Der Platz in der Reihenfolge dieser Fassung, 1 zuerst. null, wenn das Release nicht passt. */
  rank: number | null
  /**
   * Seit Schritt 3: ob dieses Release fuer diese Fassung laden kann, und sonst der erste Grund aus
   * `LoadBlock`. Ein Server von davor schickt beides nicht; dann gibt es keinen Knopf "Laden".
   */
  can_load?: boolean
  load_block?: string | null
  /** Bis wann die Automatik dieses Release warten liesse (Protokoll und Wartezeit der Fassung); null: sie wuerde sofort laden. */
  would_wait_until?: string | null
}

/** Ein gefundenes Release. ⚠️ Ohne Download- und Info-Adressen, die Oberflaeche zeigt auch keine. */
export type SearchRelease = {
  release_key: string
  title: string
  indexer_id: number
  indexer: string
  /** `torrent` oder `usenet`. */
  protocol: string
  size_bytes: number | null
  age_hours: number | null
  seeders: number | null
  peers: number | null
  grabs: number | null
  /** Merkmale wie `freeleech`, `internal`, `scene`. Die Seite zeigt sie nicht; die Regeln, die sie treffen, stehen bei den Treffern. */
  flags: string[]
  /** false: Das Release gehoert nach Titel oder Jahr zu einem anderen Film. */
  belongs: boolean
  not_this_movie: { parsed_title: string | null; parsed_year: number | null } | null
  /** null bei Serien. */
  parsed: ParsedRelease | null
  versions: SearchReleaseVersion[]
  /* Seit S3, nur bei Serien gefuellt. */
  not_this_series?: { parsed_title: string | null } | null
  parsed_series?: ParsedSeriesRelease | null
  match?: SearchMatch | null
  /** Ob das Release eine Folge meint, fuer die gesucht wurde. Nur solche kann nexcrate nehmen. */
  in_scope?: boolean
  /** Seit Schritt 3: Das Release steht auf der Sperrliste des Titels. Ein Server von davor schickt es nicht; dann gilt false. */
  blocklisted?: boolean
}

/** `GET /api/searches/{search_id}`. */
export type Search = {
  search_id: string
  title_id: number
  /** Seit S3. Ein Server von davor sucht nur Filme und schickt es nicht. */
  kind?: 'movie' | 'series' | string
  scope?: SearchScope | null
  /** Die gefragten Staffeln und Folgen, etwa `S02`. */
  targets?: string[]
  state: SearchState
  started_at: string
  finished_at: string | null
  indexers: SearchIndexer[]
  versions: SearchVersion[]
  releases: SearchRelease[]
}

/** 202 auf `POST /api/library/{id}/search`. */
export type SearchStart = { search_id: string }

/** `GET /api/trash`: welcher Stand der TRaSH Guides gilt und ob es einen neueren gibt. */
export type TrashState = {
  commit: string
  date: string
  source: 'bundled' | 'fetched'
  update_available: boolean
  latest_commit: string | null
  checked_at: string | null
  updates_enabled: boolean
  license: string
  copyright: string
  url: string
}

/* ------------------------------------------------------------------------------------------ */
/* Schritt 3, wie in the design notes unter "API": Download-Programme, Laden, Downloads,   */
/* Ordner und Benennung.                                                                       */
/* ------------------------------------------------------------------------------------------ */

export type DownloadClientKind = 'sabnzbd' | 'qbittorrent' | 'nzbget' | 'transmission' | 'deluge'

export type DownloadProtocol = 'usenet' | 'torrent'

/** Derselbe Ordner zweimal: wie das Download-Programm ihn meldet und wie nexcrate ihn sieht. */
export type PathMapping = { remote: string; local: string }

/** Das Geheimnis gibt der Server nie heraus, nur `has_secret`. Den Benutzernamen von qBittorrent schon. */
export type DownloadClient = {
  id: number
  name: string
  kind: DownloadClientKind
  protocol: DownloadProtocol
  url: string
  username: string | null
  has_secret: boolean
  category: string
  priority: number
  enabled: boolean
  path_mappings: PathMapping[]
  last_error_code: string | null
  from_source: { source_id: number; name: string } | null
  active_downloads: number
  /** Seit T2. Ein Server von davor schickt keine. */
  tags?: string[]
  /** Usenet: wie lange die Newsserver Artikel halten, aus dem Programm gelesen. null bei Torrents und vor dem ersten Lesen. */
  retention?: 'days' | 'unlimited' | 'unknown' | null
  retention_days?: number | null
}

export type DownloadClientCreate = {
  name: string
  kind: DownloadClientKind
  url: string
  /** Nur qBittorrent. */
  username?: string
  /** SABnzbds API-Schluessel oder qBittorrents Passwort. */
  secret?: string
  category?: string
  priority?: number
  enabled?: boolean
  /** Aus Radarr geholt, als Auskunft. */
  from?: { source_id: number; radarr_id: number }
  /** Seit T2: alle Tags, nach Namen. */
  tags?: string[]
}

/** Jedes Feld darf fehlen. Ohne `secret` bleibt das gespeicherte. `path_mappings` darf nur kuerzer werden. */
export type DownloadClientUpdate = Partial<Omit<DownloadClientCreate, 'from' | 'kind'>> & { path_mappings?: PathMapping[] }

/** Die Felder von POST, dazu wahlweise `id`. Dann nimmt der Server ohne `secret` das gespeicherte. */
export type DownloadClientTest = DownloadClientCreate & { id?: number }

export type DownloadClientTestResult = { version: string; category_exists: boolean }

/** Ein Download-Programm, wie Radarr es kennt. Schluessel und Passwort schickt Radarr nicht, deshalb fehlen sie. */
export type RadarrDownloadClient = {
  radarr_id: number
  name: string
  kind: DownloadClientKind
  url: string
  username: string | null
  radarr_category: string | null
  enabled: boolean
  priority: number
  already_added: boolean
  /** Seit T2: seine Tags in der App, nach Namen; sie gehen beim Holen mit. */
  tags?: string[]
}

export type DownloadState = 'queued' | 'downloading' | 'paused' | 'completed' | 'importing' | 'imported' | 'failed' | 'problem' | 'removed'

/** Ein Problem als Code mit Werten, nie als Satz. `needs_owner` trennt "Braucht dich" von "Hinweise". */
export type DownloadProblem = { code: string; needs_owner: boolean; values: Record<string, unknown> }

/** Seit dem Entpacken (T3 in the design notes) kommt `unpacked` dazu. */
export type DownloadTransfer = 'hardlink' | 'copy' | 'move' | 'unpacked'

/**
 * Was beim Ablegen gerade passiert. Nur waehrend `importing`, sonst null. Seit M4 bei Alben auch waehrend `completed`:
 * `waiting_tracks` (die Titellisten laden noch), `matching`, `fingerprinting`, `filing`.
 */
export type DownloadStep = 'unpacking' | 'waiting_tracks' | 'matching' | 'fingerprinting' | 'filing'

export type Download = {
  id: number
  /** Seit M4: `kind` und bei Alben der Kuenstler. Ein Server von davor schickt beides nicht. */
  title: { id: number; title: string; year: number | null; kind?: string | null; artist?: string | null }
  version: { id: number; label: string }
  client: { id: number; name: string; kind: DownloadClientKind } | null
  protocol: DownloadProtocol
  release: {
    title: string
    indexer_id: number | null
    indexer: string
    size_bytes: number | null
    quality: string | null
    score: number | null
    below_target: boolean
  }
  state: DownloadState
  /** Seit dem Entpacken: `unpacking`, solange `state` `importing` ist und nexcrate entpackt. Ein Server von davor schickt es nicht. */
  step?: DownloadStep | null
  /** 0 bis 100. */
  progress: number | null
  remaining_seconds: number | null
  problem: DownloadProblem | null
  transfer: DownloadTransfer | null
  /** Nur der Dateiname, nie ein ganzer Pfad. */
  imported_file: string | null
  /**
   * ⚠️ Steht nicht im Plan: Filmordner und Datei relativ zum Ordner der Fassung, etwa `Movie (2003)/Movie (2003).mkv`.
   * Schickt der Server es, nennt der Verlauf den Filmordner; sonst nur die Fassung.
   */
  imported_path?: string | null
  /** Nur bei `failed`: warum. Sonst null. */
  failed_reason: FailedReason | null
  /** Seit dem 22.09.2026, nur bei `failed`: was SABnzbd sagte. Ein Server von davor schickt es nicht. */
  failed_detail?: FailedDetail | string | null
  /** Seit dem 22.09.2026, nur bei `failed`: was daraus wurde. */
  aftermath?: DownloadAftermath | null
  confirmed: string[]
  /** Seit Schritt 3c: woher der Download kam. Ein Server von davor schickt es nicht; dann gilt er als von Hand geladen. */
  origin?: DownloadOrigin
  grabbed_at: string
  completed_at: string | null
  imported_at: string | null
  updated_at: string
  /** Seit S4: was ein Serien-Download umfasst; null bei Filmen. Ein Server von davor schickt es nicht. */
  scope?: DownloadScope | null
}

/** Der Umfang eines Serien-Downloads (S4): Folgen, abgelegt, offen, fehlten. Seit M4 auch `album`: Dateien. */
export type DownloadScope = {
  kind: 'episode' | 'season' | 'series' | 'album' | string
  season: number | null
  episodes: string[]
  /** Bei `album`: die Ausgabe, die die Dateien sind. */
  release_id?: number | null
  filed: number
  open: number
  missing: number
  /** Seit der Durchsicht: Folgen je Zustand. Ein Server von davor schickt sie nicht. */
  skipped_codes?: string[]
  not_filed_codes?: string[]
  missing_codes?: string[]
  /** Folgen, die noch keine Datei haben, solange der Download nicht fertig ist. */
  waiting_codes?: string[]
}

/** Was nexcrate aus einem Video gelesen hat. */
/** `part`: 1 oder 2, wenn nexcrate die Datei als Haelfte einer Doppelfolge gelesen hat. */
export type DownloadFileReading = { form: string | null; from: string | null; season: number | null; numbers: number[]; air_date: string | null; part?: number | null }

export type DownloadVideo = {
  key: number
  path: string
  size_bytes: number
  duration_seconds: number | null
  reading: DownloadFileReading | null
  decision: string
  episodes: { id: number; code: string; name: string }[]
}

export type DownloadEpisodeChoice = {
  id: number
  code: string
  name: string
  season: number
  in_download: boolean
  state: string | null
  watched: boolean
  /** `episodes`: jede Folge, die die vorhandene Datei enthaelt (eine Doppelfolge nur ganz ersetzen). */
  current_file: { quality: string | null; size_bytes: number; episodes?: string[]; parts?: number[] } | null
}

/** `GET /api/downloads/{id}/files`. */
export type DownloadFiles = {
  download_id: number
  kind: 'series' | 'movie' | string
  files: DownloadVideo[]
  episodes: DownloadEpisodeChoice[]
  series: { id: number | null; title?: string | null }
  version: { id: number | null; label?: string | null }
}

/** `part`: 1 oder 2 fuer eine Haelfte einer Doppelfolge, nur mit genau einer Folge; fehlt sonst. */
export type AssignBody = { files: { key: number; episode_ids: number[]; part?: 1 | 2 }[]; confirm: 'not_better'[] }

/** Eine Audiodatei eines Albendownloads im Dialog "Von Hand zuordnen" (M4.6). */
export type AlbumAudioFile = {
  key: number
  path: string
  size_bytes: number
  duration_ms: number | null
  codec: string | null
  bit_depth: number | null
  bitrate: number | null
  tags: { title: string | null; artist: string | null; album: string | null; tracknumber: string | null; discnumber: string | null }
  reading: { medium: number | null; position: number | null; source: string | null; title: string | null } | null
  decision: 'filed' | 'placing' | 'open' | 'loose' | 'other_album' | 'not_needed' | 'not_filed' | string
  /** Die Datei liegt schon im Albumordner. */
  placed: boolean
  track_id: number | null
  via: 'id' | 'position' | 'name' | 'fingerprint' | 'owner' | string | null
  proposal: number[]
  other_album: { id: number; title: string } | null
}

export type AlbumTrackChoice = {
  id: number
  medium: number
  position: number
  number: string | null
  name: string
  length_ms: number | null
  /** Was das Album fuer diesen Titel schon hat. */
  held: { quality: string | null; file: string } | null
}

export type AlbumReleaseChoice = {
  id: number
  name: string
  date: string | null
  country: string | null
  formats: string[]
  media_count: number
  track_count: number
  disambiguation: string | null
}

/** `GET /api/downloads/{id}/album-files`. */
export type AlbumFiles = {
  download_id: number
  kind: 'album'
  album: { id: number | null; title?: string | null }
  artist: string | null
  version: { id: number | null; label?: string | null }
  problem: { code: string; values: Record<string, unknown> } | null
  release_id: number | null
  read_release_id: number | null
  target_release_id: number | null
  release_fixed: boolean
  releases: AlbumReleaseChoice[]
  tracks: AlbumTrackChoice[]
  files: AlbumAudioFile[]
}

export type AlbumAssignBody = {
  release_id: number | null
  files: { key: number; track_id: number | null; loose: boolean }[]
  confirm: 'not_better'[]
}

/** `GET /api/music/albums/{id}/tags`: die Retag-Vorschau (M4.5). */
export type AlbumTagPreview = {
  title_id: number
  write_enabled: boolean
  cover: boolean
  files: {
    track_file_id: number
    file: string
    tags_state: string | null
    refusal: string | null
    changes: { field: string; before: string[]; after: string[] }[]
  }[]
  changed: number
  written?: number | null
}

export type MusicFilesSettings = { write_tags: boolean }

export type AcoustIdState = {
  enabled: boolean
  key_set: boolean
  /** Welchen Schluessel eine Abfrage nimmt: den eigenen, den mitgelieferten oder keinen. */
  key_source: 'own' | 'shipped' | null
  fpcalc_available: boolean
  last_error_code: string | null
  last_ok_at: string | null
}

/** `POST /api/downloads/takes`. */
export type TakesResult = { results: { release_key: string; download: Download | null; error: { code: string; values: Record<string, unknown> } | null }[] }

export type DownloadsView = 'active' | 'problems' | 'history'
/** Die Reiter der Seite Downloads. Die Sperrliste ist keine Ansicht der Downloads, sie hat eine eigene Route. */
export type DownloadsTab = DownloadsView | 'waiting' | 'blocklist'

/** Ein gesperrtes Release ueber alle Titel (Rueckmeldung 20.09.2026). */
export type BlocklistRow = BlocklistEntry & { title: { id: number; title: string; year: number | null; kind: MediaKind } | null }

export type BlocklistPage = { items: BlocklistRow[]; total: number; page: number; per_page: number }

/** `GET /api/downloads`. `counts` gilt fuer alle drei Ansichten, egal welche gerade kommt. */
export type DownloadList = {
  items: Download[]
  total: number
  page: number
  per_page: number
  counts: { active: number; needs_owner: number; hints: number }
}

/** Was man beim Laden ausdruecklich bestaetigt. */
export type LoadConfirmation = 'not_fitting' | 'blocklisted' | 'no_gain'

export type DownloadCreate = {
  search_id: string
  release_key: string
  /** Die Nummer der Fassung unter Einstellungen. */
  version_id: number
  confirm: LoadConfirmation[]
}

/** Warum eine Fassung nicht laden kann, in der Reihenfolge des Plans unter "Which versions can load". */
export type LoadBlock = 'version_fed_by_source' | 'version_no_profile' | 'version_no_folder' | 'no_client_for_protocol' | 'download_active' | 'episodes_downloading'

export type DownloadRemoval = { remove_from_client: boolean; blocklist: boolean }

/** Der aktive Download einer Fassung am Titel, mit dem Namen seines Programms. */
export type TitleDownload = { id: number; state: DownloadState; progress: number | null; problem_code: string | null; client_name: string | null }

export type FolderMount = { path: string; free_bytes: number; total_bytes: number }

/** `GET /api/folders` ohne Pfad. */
export type FolderMounts = { mounts: FolderMount[] }

/** `GET /api/folders?path=`. `parent` ist null an einem eingebundenen Ordner. */
export type FolderListing = {
  path: string
  parent: string | null
  folders: { name: string; path: string }[]
  free_bytes: number
  total_bytes: number
}

export type Umlauts = 'keep' | 'replace'

/** Was `PUT /api/naming` und die Vorschau nehmen. */
export type NamingPatterns = {
  /** Seit S4: die Standardmuster fuer Serien; ohne sie bleiben die gespeicherten. */
  series?: SeriesNamingPatterns & { multi_episode_style: MultiEpisodeStyle }
  /** Seit M4: die Muster fuer Musik; ohne sie bleiben die gespeicherten. */
  music?: MusicNamingPatterns
  movie_folder: string
  movie_file: string
  umlauts: Umlauts
}

export type Naming = Omit<NamingPatterns, 'series' | 'music'> & {
  defaults: { movie_folder: string; movie_file: string }
  tokens: string[]
  /** Seit der Uebernahme: je Fassung fuer Filme ihre Muster. Ein Server von davor schickt es nicht; dann gibt es keine Benennung je Fassung. */
  versions?: VersionNaming[]
  /** Seit S4: die Benennung fuer Serien. Ein Server von davor schickt sie nicht. */
  series?: SeriesNaming
  /** Seit M4: die Benennung fuer Musik. Ein Server von davor schickt sie nicht. */
  music?: MusicNaming
}

/** Seit M4 (Entscheidung 14): Kuenstlerordner, Albumordner, Datei mit einem und mit mehreren Medien. */
export type MusicNamingPatterns = { artist_folder: string; album_folder: string; track_file: string; multi_disc_file: string }

export type MusicNaming = MusicNamingPatterns & {
  defaults: MusicNamingPatterns
  tokens: string[]
  /** Ein Lidarr, dessen Benennung die Musik uebernehmen kann; null, wenn es keines gibt. */
  source_id: number | null
}

export type MultiEpisodeStyle = 'extend' | 'duplicate' | 'repeat' | 'scene' | 'range' | 'prefixed_range'
export type EpisodeNumbering = 'tmdb' | 'tvdb'

/** Seit Anime A5 mit `anime_file`, dem einzigen Muster mit `{absolute}`. */
export type SeriesNamingPatterns = { series_folder: string; season_folder: string; specials_folder: string; episode_file: string; daily_file: string; anime_file: string }

/** Lidarrs Benennung, so wie nexcrate sie liest (Rueckmeldung 20.09.2026). */
export type MusicSourceNaming = MusicNamingPatterns & {
  rename_tracks: boolean
  colon_replacement: string | null
  problems: Record<keyof MusicNamingPatterns, NamingProblem | null>
  can_take: boolean
  notes: RadarrNote[]
}

export type SeriesNamingVersion = {
  version_id: number
  label: string
  own: Record<keyof SeriesNamingPatterns, string | null>
  patterns: SeriesNamingPatterns
  episode_numbering: EpisodeNumbering
  /** Ein Sonarr, dessen Benennung diese Fassung uebernehmen kann; null, wenn es keines gibt. */
  source_id: number | null
}

export type SeriesNaming = SeriesNamingPatterns & {
  multi_episode_style: MultiEpisodeStyle
  defaults: SeriesNamingPatterns
  tokens: string[]
  folder_additions: { plex: string; jellyfin: string; emby: string }
  versions: SeriesNamingVersion[]
}

export type SeriesNamingPreview = { series_folder: string; examples: { key: string; season_folder: string; file: string }[] }

/** `file` endet mit der Endung des erfundenen Releases, etwa `.mkv`. */
export type NamingPreview = { folder: string; file: string }

/** Warum ein Download fehlgeschlagen ist: SABnzbd meldet Failed, oder der Job ist verschluesselt. */
/** Seit dem 22.09.2026 auch `not_taken`: das Programm hat die Uebergabe nicht rechtzeitig beantwortet und den Auftrag nie gezeigt. */
export type FailedReason = 'client_failed' | 'encrypted' | 'not_taken'

/** Was SABnzbd zu einem Fehlschlag sagte, als Code. Nie sein Satz. */
export type FailedDetail = 'repair_failed' | 'incomplete' | 'not_on_server' | 'password' | 'unpack_failed' | 'encrypted' | 'unwanted_extension' | 'duplicate' | 'aborted' | 'other'

/**
 * Was aus einem Fehlschlag wurde (22.09.2026): ersetzt, die Datei bleibt, wartet aufs Tageslimit, noch nichts gefunden,
 * die Ersatzsuche steht an, nach Plan, oder niemand kuemmert sich (dann gibt es eine Karte).
 */
export type DownloadAftermath = {
  kind: 'replaced' | 'kept_file' | 'waiting_limit' | 'nothing_found' | 'searching' | 'schedule' | 'owner' | string
  release: string | null
  state: string | null
  at: string | null
}

/** Ein Auftrag in der Kategorie von nexcrate, den kein Download verfolgt (22.09.2026). */
export type ForeignJob = {
  id: number
  client: { id: number; name: string; kind: DownloadClientKind } | null
  name: string
  state: 'queued' | 'downloading' | 'paused' | 'completed' | 'failed' | 'problem' | string
  progress: number | null
  size_bytes: number | null
  first_seen_at: string
  parsed: { title: string | null; year: number | null }
  proposals: Proposal[]
  /** Seit dem 22.09.2026 abends: `series`, wenn der Name eine Staffel oder Folge nennt, sonst `movie`. Eine Vermutung. */
  kind?: MediaKind
  /** Serien der Bibliothek, auf die der Name passt. */
  series_proposals?: { title_id: number; title: string; year: number | null; kind: string }[]
}

export type ForeignJobList = { items: ForeignJob[] }

/** Genau eines von `title_id` und `tmdb_id`. */
export type ForeignAdopt = { title_id?: number; tmdb_id?: number; version_id: number }

/** Ein gesperrtes Release eines Titels. `reason` ist ein Code wie `client_failed`, `encrypted` oder `dangerous_file`. */
export type BlocklistEntry = { id: number; release_title: string; indexer: string; reason: string; created_at: string }

/* ------------------------------------------------------------------------------------------ */
/* Uebernahme aus Radarr, Benennung je Fassung und Papierkorb, wie in the design notes   */
/* unter "API".                                                                                 */
/* ------------------------------------------------------------------------------------------ */

export type TakeoverKind = 'check' | 'takeover'

export type TakeoverState = 'running' | 'done' | 'failed'

export type TakeoverPhase = 'reading' | 'mapping' | 'files' | 'saving' | 'reading_folders' | 'companions'

/**
 * Wie ein Ordner aus Radarr zu seinem Ordner in nexcrate kam. `derived` seit Befund 13: bei einem Ordner ohne Dateien,
 * abgeleitet aus der Zuordnung eines anderen Ordners.
 */
export type TakeoverFoundBy = 'same_path' | 'found' | 'chosen' | 'derived' | 'none'

/** Was eine Uebernahme ohne Bestaetigung ablehnt. `root_unmapped` laesst sich nicht bestaetigen, nur beheben. */
export type TakeoverBlocker = 'root_unmapped' | 'files_missing' | 'queue_active'

export type TakeoverRoot = {
  /** Der Stammordner, wie Radarr ihn sieht. */
  remote: string
  /** Derselbe Ordner, wie nexcrate ihn sieht. null ohne Zuordnung. */
  local: string | null
  found_by: TakeoverFoundBy
  movies: number
  /** 0 seit Befund 13: Radarr hat dort nur Filme ohne Datei. Dann ist die Zuordnung freiwillig und sperrt nie. */
  files: number
  samples: number
  samples_matched: number
}

/** nexcrates Fassung, die diese Verbindung fuellt. `folder_proposal` nur, wenn sie keinen Ordner hat. */
export type TakeoverVersion = { id: number; label: string; has_profile: boolean; folder: string | null; folder_proposal: string | null }

/** Der erste Einwand von nexcrates Pruefung gegen ein Muster, als Code mit Werten wie bei einem Fehler. */
export type NamingProblem = { code: string; values?: Record<string, unknown> }

/** Ein Hinweis zu Radarrs Einstellungen, als Code mit Werten. Sperrt nie etwas. */
export type RadarrNote = { code: string; values?: Record<string, unknown> }

/** `GET /api/sources/{id}/naming`: Radarrs Benennung. Gespeichert wird nichts. */
export type SourceNaming = {
  movie_folder: string
  movie_file: string
  rename_movies: boolean
  colon_replacement: string
  problems: { movie_folder: NamingProblem | null; movie_file: NamingProblem | null }
  /**
   * Was Radarr anders macht, nie sperrend: `radarr_no_rename` (dann ist `movie_file` `{Original Title}`),
   * `slash_in_pattern {pattern}`, `colon_format {format}`, `illegal_characters_kept`.
   */
  notes?: RadarrNote[]
  /** true, wenn nexcrate beide Muster so annimmt. Haengt nur an `problems`. */
  can_take: boolean
}

/** `GET /api/sources/{id}/naming` einer Sonarr-Verbindung (S6, Entscheidung 13). */
export type SeriesSourceNaming = {
  series_folder: string
  season_folder: string
  specials_folder: string
  episode_file: string
  daily_file: string
  /** Seit Anime A5: Sonarrs Muster fuer Anime. */
  anime_file?: string
  rename_episodes: boolean
  colon_replacement: string | null
  multi_episode_style: string | null
  problems: {
    series_folder: NamingProblem | null
    season_folder: NamingProblem | null
    specials_folder: NamingProblem | null
    episode_file: NamingProblem | null
    daily_file: NamingProblem | null
    anime_file?: NamingProblem | null
  }
  can_take: boolean
  /** `sonarr_no_rename`, `slash_in_pattern {pattern}`, `colon_format {format}`, `illegal_characters_kept`, `style_differs {style}`. */
  notes?: RadarrNote[]
}

export type TakeoverTaken = { versions: number; with_file: number; missing: number; titles: number }

export type TakeoverResult = {
  /** Die App der Verbindung; ohne Angabe Radarr. */
  app?: 'radarr' | 'sonarr' | 'lidarr'
  /** `fresh`: gerade gelesen. `stored`: die App hat nicht geantwortet, es gilt der letzte Import. */
  data_from: 'fresh' | 'stored'
  read_at: string
  movies: number
  with_file: number
  /** Sonarr: seine Serien. */
  series?: number | null
  /** Sonarr: seine Folgendateien. */
  episode_files?: number | null
  /** Lidarr (Musik M6): seine Alben, ihre Dateien, ueberwachte ohne Datei und solche mit fehlenden Titeln. */
  albums?: number | null
  album_files?: number | null
  /** Musik M6: Dateien ausserhalb ihres Albumordners; sie bleiben liegen, ihre Titel zaehlen als fehlend. */
  files_outside?: number | null
  wanted?: number | null
  incomplete?: number | null
  /** Sonarr: Dateien, zu denen keine TMDB-Folge passte; danach unklare Dateien der Serie. */
  unclear?: number | null
  /** Sonarr: gefundene Dateien, die nexcrates Regeln unter dem Ziel sehen; null ohne Profil. */
  would_upgrade?: number | null
  roots: TakeoverRoot[]
  /** Zaehlt `files_other_size` mit. */
  files_found: number
  files_missing: number
  files_other_size: number
  /** Hoechstens 20, relativ zum Stammordner. */
  missing_examples: string[]
  queue: number
  version: TakeoverVersion
  naming: SourceNaming | SeriesSourceNaming | null
  /**
   * Aus Radarrs Einstellungen fuer Medien, nie sperrend: `radarr_recycle_bin {days}`, `radarr_extra_files`,
   * `radarr_hardlinks_off`, `radarr_file_date`, `radarr_permissions`. Leer, wenn sie nicht lesbar waren.
   */
  notes?: RadarrNote[]
  /** Meist Codes aus `TakeoverBlocker`. Einen unbekannten nimmt die Seite als Sperre. */
  blockers: string[]
  /** Nur nach einer Uebernahme. */
  taken: TakeoverTaken | null
  /** Since the library-from-disk block: the counts of the `release.nex` phase after a takeover, null for a check. */
  companions?: Record<string, number> | null
}

/** Pruefung oder Uebernahme, im Speicher des Servers bis 30 Minuten nach dem Ende. */
export type TakeoverJob = {
  id: number
  source_id: number
  kind: TakeoverKind
  state: TakeoverState
  phase: TakeoverPhase | null
  /** Angesehene Dateien. null vor dieser Phase. */
  progress: { done: number; total: number } | null
  started_at: string
  finished_at: string | null
  error_code: string | null
  error_values: Record<string, unknown> | null
  result: TakeoverResult | null
}

/** Die Zuordnungen, die der Besitzer selbst gewaehlt hat. Ohne Koerper ordnet nexcrate alles selbst zu. */
export type TakeoverCheckRequest = { mappings: PathMapping[] }

export type TakeoverRequest = {
  mappings: PathMapping[]
  accept_missing: boolean
  accept_queue: boolean
  take_naming: boolean
  /** Nur fuer eine Fassung ohne Ordner. */
  folder: string | null
  /** 24.09.2026: Was die Verbindung bringt, wird nicht verbessert (Fassungen in Ruhe, Folgen aus). */
  keep_as_is: boolean
}

/**
 * `POST /api/sources/{id}/takeover/undo` (Befund 12). Ohne `api_key` nimmt der Server den gespeicherten Schluessel;
 * einen gegebenen prueft er wie beim Anlegen einer Verbindung.
 */
export type TakeoverUndoRequest = { api_key?: string }

/** Die Benennung einer Fassung fuer Filme: ihre eigenen Muster oder die der Standard-Benennung. */
export type VersionNaming = {
  version_id: number
  label: string
  own: boolean
  movie_folder: string
  movie_file: string
  /** Eine Verbindung zu Radarr, die diese Fassung fuellt und nicht uebernommen ist. Fuer "Aus Radarr übernehmen". */
  source_id: number | null
}

export type VersionNamingPatterns = { movie_folder: string; movie_file: string }

/** `GET /api/recycle`: wie viele Tage ersetzte Dateien im Papierkorb bleiben. */
export type Recycle = { days: number }

/** `GET /api/recycle/bin`: eine geloeschte Datei. `deleted_by` ist `owner` oder `key` (ein Programm, `deleted_by_name`). */
export type RecycleBinEntry = {
  id: number
  title_id: number | null
  kind: MediaKind
  title: string
  year: number | null
  version_label: string
  season: number | null
  episodes: number[]
  file_name: string
  size_bytes: number
  deleted_at: string
  deleted_by: 'owner' | 'key'
  deleted_by_name: string | null
  /** false: die Datei ist weg oder ihre Platte gerade nicht zu sehen. */
  present: boolean
  /** false: Titel oder Fassung sind nicht mehr in der Bibliothek; zurueck geht dann nichts. */
  in_library: boolean
}

export type RecycleBin = { items: RecycleBinEntry[]; size_bytes: number }

/** `POST /api/library/{id}/delete-files`: Film-Fassung ganz, Serien-Fassung ganz, eine Staffel oder eine Folgendatei. */
export type DeleteFilesRequest = { version_id: number; season?: number; episode_file_id?: number; track_file_id?: number }

export type DeletedFiles = { files: number; missing: number }

/* ------------------------------------------------------------------------------------------ */
/* Schritt 3c, wie in the design notes unter "API": der Schalter fuer alles Automatische, */
/* Untertitel, Tageslimit und Nutzung je Indexer, der Plan je Titel und die Herkunft eines      */
/* Downloads. Jedes neue Feld an einer alten Form ist optional: Ein Server von davor fehlt es.  */
/* ------------------------------------------------------------------------------------------ */

/** `GET /api/automatic`: neue Releases lesen, nach Plan suchen, Ersatz nach Fehlschlag und automatisch laden. Ab Werk aus. */
/** `GET /api/automatic`: je ein Schalter fuer Filme und fuer Serien (S5), beide ab Werk aus. */
export type AutomaticState = { enabled: boolean; series_enabled?: boolean; music_enabled?: boolean }

/** `POST /api/library/remove` (24.09.2026): mehrere Titel auf einmal entfernen. */
export type RemovedMany = { removed: number; kept_fed: number; files: number; failed: number }

/** `GET`/`PUT /api/automatic/upgrades` (24.09.2026): Pause fuer Vorhandenes und Obergrenze pro Tag. */
export type UpgradesState = { paused: boolean; paused_since: string | null; per_day: number; used: number }

/** `GET /api/subtitles`: Untertitel-Dateien aus dem Download neben den Film legen. Ab Werk an. */
export type SubtitlesState = { enabled: boolean }

/** 202 auf `POST /api/library/{id}/search/automatic`. */
export type AutomaticSearchStart = { started: boolean }

/** Was ein Indexer ueber `newznab:apilimits` zuletzt gemeldet hat. Jeder Wert darf fehlen. Zeiten in UTC. */
export type IndexerUsage = {
  api_current: number | null
  api_max: number | null
  grab_current: number | null
  grab_max: number | null
  api_next_at: string | null
  grab_next_at: string | null
  seen_at: string | null
}

/** Das Lesen neuer Releases: zuletzt gelesen, das neueste gelesene Release, und wann eine Luecke blieb. */
export type IndexerRss = { last_at: string | null; newest_at: string | null; gap_at: string | null }

/** Woher ein Download kam: von Hand, aus einer geplanten Suche oder "Jetzt automatisch suchen", aus RSS, als Ersatz nach einem Fehlschlag. */
export type DownloadOrigin = 'manual' | 'search' | 'rss' | 'replacement'

/** Warum die naechste Suche eines Titels dann kommt, oder warum keine. Die Seite hat fuer jeden einen Satz. */
export type SearchPlanReason = 'anchor' | 'air_date' | 'schedule' | 'limit' | 'replacement' | 'replacement_limit' | 'no_date' | 'nothing_wanted' | 'off' | 'wish'

/** Womit der Plan rechnet: digitale oder physische Veroeffentlichung, Kinostart plus 90 Tage, nur das Jahr, nichts. */
export type SearchAnchorKind = 'digital' | 'physical' | 'theatrical' | 'year' | 'none' | 'release'

/** `date` ist ein Kalendertag wie "2026-08-20". `country` ist null beim allgemeinen Anker, dem fruehesten Tag in irgendeinem Land. */
export type SearchAnchor = { date: string | null; kind: SearchAnchorKind; country: string | null }

/** Ein Indexer der letzten Suche. `code` ist null, wenn er geliefert hat, sonst ein Indexer-Code. */
export type SearchSummaryIndexer = { id: number; name: string; code: string | null }

/** Je Fassung das beste Release der letzten Suche, ohne Link, mit hoechstens fuenf Ablehnungen. */
export type SearchSummaryVersion = {
  version_id: number
  label: string
  best_title: string | null
  codes: string[]
  loaded: boolean
  /** Warum das beste Release nicht geladen wurde. null, wenn es geladen wurde oder nichts dagegen sprach. */
  load_code: string | null
}

export type SearchSummary = {
  at: string
  /** `search`, `rss` oder `replacement`. */
  origin: DownloadOrigin
  releases: number
  indexers: SearchSummaryIndexer[]
  versions: SearchSummaryVersion[]
}

/** `search_plan` am Titel. `automatic` ist der Schalter, `wanted` heisst: Mindestens eine Fassung will etwas. */
export type SearchPlan = {
  automatic: boolean
  wanted: boolean
  last_at: string | null
  next_at: string | null
  reason: SearchPlanReason | null
  anchor: SearchAnchor | null
  summary: SearchSummary | null
  /** Nur bei Serien (S5): eine Zeile je Staffel, die etwas will. */
  seasons?: SeasonPlanLine[]
  /** Nur bei Serien (S6): was eine eigene Fassung zurueckhaelt. */
  held?: SearchHold[]
}

/**
 * S6: `reading_files` heisst, der Serienordner wird noch eingelesen; `unclear_files`, dass Dateien ohne Folge die
 * Folgen zurueckhalten, die vor `since` gelaufen sind.
 */
export type SearchHold = {
  version_id: number
  reason: 'reading_files' | 'unclear_files'
  count: number
  since: string | null
}

/** Ein Paket, das nexcrate nach der Regel des Besitzers nicht von selbst laedt: Groesse und wie viele Folgen es ersetzen wuerde. */
export type SeasonPackOnly = { size: number | null; episodes: number }

/** Was die letzte automatische Suche oder RSS fuer eine Fassung in einer Staffel tat. */
export type SeasonResultVersion = {
  version_id: number
  label: string
  loaded: number
  filled: number
  replaced: number
  not_found: number
  no_fit: number
  codes: string[]
  pack_only: SeasonPackOnly | null
  load_code: string | null
}

export type SeasonResult = { season: number; at: string; versions: SeasonResultVersion[] }

/** Eine Staffel im Plan der Serie. `season` 0 sind die Specials. */
export type SeasonPlanLine = {
  season: number
  next_at: string | null
  reason: SearchPlanReason | null
  missing: number
  upgrades: number
  waiting: number
  no_date: number
  last_at: string | null
  result: SeasonResult | null
}

/** Eine Untertitel-Datei neben der Datei einer Fassung. `language` ISO 639-1 oder null, wenn sie unbekannt ist. */
export type Subtitle = { language: string | null; forced: boolean; sdh: boolean; file: string }

/* ------------------------------------------------------------------------------------------ */
/* Library from disk, as in the design notes under "API": folders on disk,     */
/* companion files (`release.nex`), media data. Field names are the contract with the server.  */
/* ------------------------------------------------------------------------------------------ */

/** Where a stored quality came from. `radarr` for a taken-over file, `media` when the measured resolution decided. */
export type QualityFrom = 'name' | 'media' | 'radarr'

/**
 * The state of a version's `release.nex` (L1 "Writing" and L3): written or current on disk, or why nexcrate did not
 * write it. A code this interface does not know is shown with a general sentence.
 */
export type CompanionState =
  | 'current'
  | 'written'
  | 'missing'
  | 'outdated'
  | 'other_installation'
  | 'newer_format'
  | 'broken'
  | 'foreign'
  | 'changed'
  | 'not_writable'
  | 'file_missing'
  | 'folder_missing'
  | 'no_space'
  | 'failed'

/** `companion` on a title's version. `written_at` is null when nothing was written yet. */
export type VersionCompanion = { state: CompanionState | string; written_at: string | null }

/** Radarr's names for the dynamic range, or null for SDR. */
export type DynamicRange = 'DV' | 'DV HDR10' | 'DV HDR10Plus' | 'DV HLG' | 'DV SDR' | 'HDR10' | 'HDR10Plus' | 'HLG' | 'PQ'

export type MediaVideo = {
  codec: string | null
  width: number | null
  height: number | null
  bit_depth: number | null
  dynamic_range: DynamicRange | string | null
  dv_profile: string | number | null
}

/** One audio stream. `channels` as `n.1` or `n.0`, `language` ISO 639-1 or null for an unknown one. */
export type MediaAudio = { codec: string | null; channels: string | null; language: string | null }

/** `versions.media_info` (L7), nexcrate's own shape, independent of the tool. */
export type MediaInfo = {
  schema: number
  tool: string
  tool_version: string | null
  container: string | null
  title_tag: string | null
  duration_seconds: number | null
  video: MediaVideo | null
  audio: MediaAudio[]
  /** ISO 639-1 codes of the subtitle streams inside the file. */
  subtitles: string[]
}

/** A scan row of a folder on disk that carries a TMDB number (L8), attached to a TMDB search result. */
export type OnDiskFolder = { folder_id: number; root: string; name: string; state: DiskFolderState | string }

/**
 * The states of a scanned folder (L4 "Matching"). `ignored` is a mark of the owner that survives new scans and is
 * listed as a state of its own.
 */
export type DiskFolderState =
  | 'library'
  | 'moved'
  | 'radarr'
  | 'sonarr'
  | 'lidarr'
  | 'restorable'
  | 'proposal'
  | 'unknown'
  | 'conflict'
  | 'file'
  | 'disc'
  | 'no_video'
  | 'unreadable'
  | 'ignored'

/** The counts of a root's last scan, per state. A state that did not occur may be missing. */
export type DiskCounts = Partial<Record<DiskFolderState, number>>

/** A scanned folder ("root"): a version's default folder, a root folder of an owned version, or one the owner added. */
/** Ob unter der Wurzel Filmordner, Serienordner (S6, Entscheidung 25) oder Kuenstler- und Albumordner (Musik M6) liegen. */
export type DiskRootKind = 'movie' | 'series' | 'album'

export type DiskRoot = {
  id: number
  path: string
  kind?: DiskRootKind
  /** The version definitions this root belongs to. Empty for a folder the owner added that belongs to none. */
  versions: { id: number; label: string }[]
  added_by_owner: boolean
  last_scan_at: string | null
  /** null before the first scan. */
  counts: DiskCounts | null
  /** Own files whose video was not found in this root (decision 25). */
  missing_files: number
  /** Names of Radarr connections whose root folder could not be mapped into this root. */
  radarr_unmapped: string[]
  /** `folder_not_visible` for a root that is gone, else null. */
  error_code: string | null
}

export type DiskJobKind = 'scan' | 'restore' | 'assign'

export type DiskJobState = 'running' | 'done' | 'failed'

/** The scan's phases; `restore` and `assign` jobs use their own. An unknown phase shows a general sentence. */
export type DiskJobPhase = 'listing' | 'folders' | 'radarr' | 'matching' | 'tmdb'

/**
 * The result of a finished job: counts per outcome, for `restore` and `assign` also `conflicts` with up to 20 folder ids.
 * The counts may come nested under `counts` or at the top level; the page reads both.
 */
export type DiskJobResult = { counts?: Record<string, number>; conflicts?: number[] } & Record<string, unknown>

/** A scan, restore or assign job, kept in memory by the server for 30 minutes after it ends. */
export type DiskJob = {
  id: number
  kind: DiskJobKind
  state: DiskJobState
  phase: DiskJobPhase | string | null
  progress: { done: number; total: number } | null
  started_at: string
  finished_at: string | null
  error_code: string | null
  result: DiskJobResult | null
}

/** `GET /api/disk`. */
export type DiskOverview = { roots: DiskRoot[]; job: DiskJob | null; tmdb_ready: boolean }

/** A video inside a scanned folder, largest first. `name_quality` is the quality its name gives, or null. */
export type DiskVideo = { name: string; size_bytes: number; name_quality: string | null }

/** How a proposal was found (decision 17). */
export type ProposalFrom = 'companion' | 'name_number' | 'nfo_number' | 'title_year' | 'library'

export type Proposal = {
  tmdb_id: number
  imdb_id: string | null
  title: string
  year: number | null
  /** TMDB's poster file, served by nexcrate under `/api/tmdb/poster/w185/{file}`. null without a poster. */
  poster_file: string | null
  from: ProposalFrom | string
  /** The title in the library, when the movie is there already. */
  title_id: number | null
  unambiguous: boolean
}

/** One entry of a read `release.nex`, without subtitles (L1). */
export type CompanionEntry = {
  version: string
  file: string
  size_bytes?: number | null
  quality?: string | null
  release_title?: string | null
}

/** The read outcome of a folder's `release.nex` with its numbers, name, year and entries. */
export type DiskCompanion = {
  outcome: 'ours' | 'other_installation' | 'newer_format' | 'broken' | 'foreign' | string
  tmdb_id: number | null
  imdb_id: string | null
  title: string | null
  year: number | null
  entries: CompanionEntry[]
}

export type DiskFolderKind = 'folder' | 'group' | 'file' | 'disc'

/** A row of `GET /api/disk/folders`. */
export type DiskFolder = {
  id: number
  root_id: number
  /** The folder as on disk, relative to the root; for a `file` row the video's name. */
  relative_path: string
  kind: DiskFolderKind
  state: DiskFolderState
  ignored: boolean
  videos: DiskVideo[]
  parsed: { title: string | null; year: number | null } | null
  companion: DiskCompanion | null
  proposals: Proposal[]
  /** Ein Serienordner (S6): wie viele Videos das Einlesen ansehen wuerde, die Staffelordner, die Nummer der release.nex. */
  series?: { videos: number; seasons: string[]; companion_tmdb_id: number | null } | null
  /** Ein Albumordner (Musik M6): was der Ordner sagt, und die Vorschlaege; `proposals` ist dann leer. */
  album?: DiskAlbum | null
  /** The title in the library for `library`, `moved` and `conflict` rows. */
  title_id: number | null
  /** The Radarr connection for a `radarr` row. */
  source_name: string | null
  seen_at: string
}

export type DiskFolderList = { total: number; items: DiskFolder[] }

/** Musik M6: ein Vorschlag fuer einen Albumordner. Ohne `title_id` wird das Album erst ueber MusicBrainz hinzugefuegt. */
export type DiskAlbumProposal = {
  title_id: number | null
  mbid: string | null
  title: string
  artist: string | null
  year: number | null
  from: 'companion' | 'tags' | 'library'
  unambiguous: boolean
}

/** Musik M6: was ein Albumordner sagt. */
export type DiskAlbum = {
  audio: number
  artist: string | null
  album: string
  release_group: string | null
  release: string | null
  from: 'companion' | 'tags' | null
  proposals: DiskAlbumProposal[]
}

/** `POST /api/disk/folders/{id}/media`. `error_code` is `media_unreadable`, `media_timeout` or `media_truncated` (cut off); the name's quality then. */
export type DiskMediaResult = {
  media: MediaInfo | null
  quality: string | null
  quality_from: QualityFrom | null
  languages: string[]
  error_code: string | null
}

/** `POST /api/disk/folders/{id}/assign`. `version_id` is the number of the version under Settings. */
/** `keep_as_is` (24.09.2026): was der Ordner bringt, wird nicht verbessert. */
export type DiskAssignRequest = { tmdb_id: number; version_id: number; file: string; keep_as_is?: boolean }

/** `POST /api/disk/restore`: chosen rows, or every restorable row. */
export type DiskRestoreRequest = ({ folder_ids: number[] } | { all: true }) & { keep_as_is?: boolean }
/** Assigning many at once: the listed rows, or every row with an unambiguous proposal. */
export type DiskAssignManyRequest = ({ folder_ids: number[] } | { all: true }) & { keep_as_is?: boolean }

export type CompanionJobKind = 'check' | 'backfill'

/** An example folder of the report, with the ids "Ersetzen" and the title page need. */
export type CompanionExample = { version_id: number; title_id: number; folder: string }

export type CompanionJobResult = {
  versions: number
  counts: Partial<Record<CompanionState, number>> & Record<string, number>
  examples: Partial<Record<CompanionState, CompanionExample[]>> & Record<string, CompanionExample[] | undefined>
}

/** A check or backfill of `release.nex` files (L3), kept 30 minutes after it ends. */
export type CompanionJob = {
  id: number
  kind: CompanionJobKind
  state: DiskJobState
  progress: { done: number; total: number } | null
  started_at: string
  finished_at: string | null
  error_code: string | null
  result: CompanionJobResult | null
}

/** `GET /api/companions` and the answer of `PUT /api/companions`. */
export type CompanionsState = { enabled: boolean; job: CompanionJob | null }

/** `POST /api/companions/replace`: `written`, or the state that stopped the write. */
export type CompanionReplaceResult = { state: CompanionState | string }

/* ------------------------------------------------------------------------------------------ */
/* Series S1, as in the design notes under "API": series, seasons, episodes, watching, */
/* reading Sonarr. Every new field on an old shape is optional: an older server lacks it.      */
/* ------------------------------------------------------------------------------------------ */

export type SeriesType = 'standard' | 'daily' | 'anime'

/** The rule of a series version (decision 9). */
export type WatchRule = 'all' | 'future' | 'missing' | 'from_season' | 'none'

export type SeriesVersionChoice = { version_id: number; rule: WatchRule; from_season?: number | null }

/** The counts of a series version, stored on the version. */
export type EpisodeCounts = {
  /** Episodes with a file in this version. */
  files: number
  /** Watched episodes that aired and have a file. */
  have: number
  aired_watched: number
  wanted: number
  watched: number
  total: number
  /** Folgen mit Datei, die das Profil noch verbessern wuerde (S2). Ein Server von davor schickt es nicht. */
  upgrade?: number
  /** YYYY-MM-DD of the next watched episode that has not aired. */
  next_air_date: string | null
  /** Seit S4 zaehlen nur regulaere Folgen; ueberwachte, gelaufene Specials ohne Datei stehen hier. Ein Server von davor schickt es nicht. */
  specials_missing?: number
}

export type SeriesWatch = {
  /** null for a version a Sonarr connection feeds. */
  rule: WatchRule | null
  from_season: number | null
  /** The owner switched seasons or episodes by hand since the rule was applied. */
  custom: boolean
  /** A Sonarr connection feeds the version; nothing can be changed here. */
  fed: boolean
}

export type SeasonVersionBrief = {
  version_id: number
  /** The season's switch. */
  watched: boolean
  files: number
  have: number
  aired_watched: number
  watched_episodes: number
  /** Seit S4: Folgen, die ein Download dieser Fassung haelt. Ein Server von davor schickt es nicht. */
  loading?: number
}

export type SeasonBrief = {
  id: number
  /** 0 holds the specials. */
  number: number
  name: string
  air_date: string | null
  episodes: number
  aired: number
  tmdb_gone: boolean
  versions: SeasonVersionBrief[]
}

export type NumberingPart = { seasons: number; episodes: number; sizes: number[] }

/** How a Sonarr connection counts against TMDB (decision 25). */
export type NumberingNote = {
  tmdb: NumberingPart
  source: NumberingPart & { name: string }
  /** Seit S4 mit den Specials, die TMDB nicht kennt; davon `unmatched_specials`. */
  unmatched: number
  unmatched_specials?: number
  /** Every regular season both count differently. Missing in notes stored before the next import. */
  differences?: { season: number; tmdb: number; source: number }[]
}

/** Warum eine Datei beim Einlesen keine Folge bekam (S6, Ue3). */
export type UnclearReason =
  | 'no_numbers'
  | 'unknown_numbers'
  | 'ambiguous'
  | 'counted_otherwise'
  | 'episode_has_file'
  | 'duplicate'

/** Eine Folge ohne Datei in der Naehe der Stelle, die die Datei nennt. */
export type NearbyEpisode = { id: number; season: number; episode: number; name: string | null; air_date: string | null }

/** Die Datei, die eine Folge in dieser Fassung gerade hat. */
export type OccupyingFile = { id: number; relative_path: string; size_bytes?: number | null; quality?: string | null }

/** Seit 18.09.2026: eine Folge, als die sich eine Datei liest, die aber schon eine andere Datei hat. */
export type OccupiedEpisode = NearbyEpisode & { file: OccupyingFile }

/** Seit 18.09.2026: jede Folge der Fassung fuer "Andere Folge", mit der Datei, die sie hat. */
export type EpisodeChoice = NearbyEpisode & { file: OccupyingFile | null }

/**
 * Der Vorschlag von nexcrate (S6, Entscheidung 16). `step`: 1 Titel und Sendedatum, 2 Titel, 3 Sendedatum, 4 Nummern.
 * Nur 1 und 2 nimmt "Alle Vorschlaege uebernehmen".
 */
export type FileProposal = {
  episode_ids: number[]
  step: number
  /** Nur was die Folge einer Quelle selbst nennt, wird ungefragt uebernommen (S6, Entscheidung 17). */
  safe?: boolean
}

export type UnassignedFile = {
  id: number
  version_id: number
  relative_path: string
  size_bytes: number
  quality: string | null
  source_numbers: { season?: number | null; episodes?: number[] } | null
  /** Beim Einlesen von der Platte: was der Name sagte. */
  read_as?: { season?: number | null; episodes?: number[]; numbering?: string | null; reason?: UnclearReason | string | null } | null
  /** Der Besitzer hat gesagt, sie gehoert zu keiner Folge. */
  left_out?: boolean
  /** Sonarrs Folge mit diesen Nummern, wenn Sonarr eine kannte. */
  source_episode?: { season: number; episode: number; name: string | null; air_date: string | null } | null
  /** Nur bei einer eigenen Fassung. */
  proposal?: FileProposal | null
  /** Nur bei einer eigenen Fassung: Folgen ohne Datei um die Stelle herum, hoechstens 12. */
  nearby?: NearbyEpisode[]
  /** Seit 18.09.2026: die Folgen, als die sich die Datei liest und die schon eine Datei haben. */
  occupied?: OccupiedEpisode[]
}

/** Eine eigene Serienfassung, deren Serienordner noch eingelesen wird (S6, Entscheidung 23). */
export type FolderReading = { version_id: number; state: 'queued' | 'reading'; done: number; total: number | null }

export type EpisodeGroup = { id: string; name: string; type: number | null; episode_count: number | null; group_count: number | null }

export type SeriesBlock = {
  type: SeriesType | string
  /**
   * B1: die Verbindung, die die Art bestimmt, sonst null. Eine Serie, die eine Sonarr-Verbindung fuellt, nimmt bei
   * jedem Lauf deren Art; die Oberflaeche zeigt sie dann fest.
   */
  type_fed: string | null
  /** TMDB's status as it comes. */
  status: string | null
  networks: string[]
  first_air_date: string | null
  last_air_date: string | null
  next_air_date: string | null
  tvdb_id: number | null
  numbering: NumberingNote | null
  late_episodes: number
  episode_groups: EpisodeGroup[]
  seasons: SeasonBrief[]
  unassigned_files: UnassignedFile[]
  /** S6: eigene Fassungen, deren Ordner noch nicht eingelesen ist; bis dahin wollen sie nichts. */
  reading?: FolderReading[]
}

/** `POST /api/library/preview`. */
export type SeriesPreview = {
  seasons: number
  episodes: number
  aired: number
  proposed_type: SeriesType | string
  tmdb_type: string | null
  versions: {
    version_id: number
    watched: number
    aired: number
    /** S6, Entscheidung 31: der Serienordner liegt schon da und wird beim Hinzufuegen eingelesen. */
    on_disk?: { folder: string; videos: number } | null
  }[]
}

export type EpisodeFile = {
  id: number
  relative_path: string
  /** Der Name des Releases vor dem Umbenennen, oder null. Zeigt, welche Folge das Release selbst zu sein behauptete. */
  release_title: string | null
  size_bytes: number
  quality: string | null
  release_group: string | null
  languages: string[]
  /** How many episodes the file covers in its version. */
  episodes: number
  /** Doppelfolge in zwei Dateien: diese Datei ist Teil 1, das hier Teil 2. */
  second_part?: { id: number; relative_path: string; release_title: string | null; size_bytes: number; quality: string | null } | null
}

export type EpisodeNumber = {
  scheme: 'tvdb' | 'scene' | string
  season: number | null
  episode: number | null
  episode_end: number | null
  absolute: number | null
  verified: boolean
}

export type EpisodeState = 'problem' | 'downloading' | 'available' | 'wanted' | 'unmonitored'

export type EpisodeInVersion = {
  version_id: number
  state: EpisodeState | string
  watched: boolean
  set_by: 'rule' | 'owner' | 'source' | 'late' | string
  late: boolean
  /** For a fed version: whether Sonarr has the episode. null otherwise. */
  in_source: boolean | null
  progress: number | null
  problem_code: string | null
  file: EpisodeFile | null
}

export type Episode = {
  id: number
  season_number: number
  number: number
  air_date: string | null
  aired: boolean
  /** Seit S4 zeigt ein Platzhalter ("Folge 9") den englischen Namen. */
  name: string
  /** Seit 18.09.2026: TMDBs englischer Titel, wenn er vom angezeigten abweicht; so heissen meist die Dateien. */
  name_en?: string | null
  /** Seit S4, nur Specials: ob Sonarr das Special kennt; null ohne Fassung aus Sonarr. */
  known_to_source?: boolean | null
  overview: string | null
  runtime_min: number | null
  episode_type: string | null
  tmdb_gone: boolean
  numbers: EpisodeNumber[]
  versions: EpisodeInVersion[]
}

/** `GET /api/library/{id}/seasons/{season_id}` and the answer of every switch. */
export type SeasonEpisodes = { season_id: number; number: number; episodes: Episode[] }

export type WatchRequest = { rule: WatchRule; from_season?: number | null }

/** `POST /api/library/{id}/versions/{version_id}/watch/preview`. */
export type WatchChange = { added: number; removed: number; overridden: number; watched: number; aired: number }

export type SwitchRequest = { version_id: number; watched: boolean }

/** The progress and counts of a Sonarr import run, or (Musik M1) of a Lidarr import run. */
export type ImportRunDetails = {
  phase?: 'reading' | 'series' | string
  done?: number
  total?: number
  series?: number
  skipped?: number
  not_on_tmdb?: { title: string; tvdb_id: number | null }[]
  not_on_tmdb_count?: number
  episodes_matched?: number
  episodes_unmatched?: number
  files_unmatched?: number
  steps?: Record<string, number>
  /** Musik M1 (Lidarr): artists read. Distinguishes a Lidarr run's details from a Sonarr or Radarr one. */
  artists?: number
  /** Musik M1 (Lidarr): albums read. */
  albums?: number
  /** Musik M1 (Lidarr): releases read. */
  releases?: number
  /** Musik M1 (Lidarr): tracks read. */
  tracks?: number
  /** Musik M1 (Lidarr): track files read. */
  files?: number
  /** Musik M1 (Lidarr): artists a scheduled run did not read again, their numbers in Lidarr were unchanged. */
  unchanged?: number
  /** Musik M1 (Lidarr): files Lidarr holds that map to no track. */
  unmapped_files?: number
  /** Musik M1 (Lidarr): up to 50 paths of such files. */
  unmapped_paths?: string[]
}

/* ------------------------------------------------------------------------------------------ */
/* Serien S3, wie in the design notes unter "API": Nummerierung einer Serie, TheXEM.     */
/* ------------------------------------------------------------------------------------------ */

/** Eine lokale Korrektur: Im Release heisst die Folge `season` und `episode`. `code` ist ihre Nummer bei TMDB. */
/** B6: `season` und `episode` sind null, wenn nur die Durchzaehlnummer (`absolute`, nur Anime) korrigiert ist. */
export type NumberingCorrection = { episode_id: number; code: string; season: number | null; episode: number | null; episode_end: number | null; absolute?: number | null }

/** `GET /api/series/{id}/numbering`. */
export type SeriesNumbering = {
  title_id: number
  episode_groups: EpisodeGroup[]
  /** null: wie TMDB selbst zaehlt. */
  episode_group_id: string | null
  aliases: string[]
  title_en: string | null
  corrections: NumberingCorrection[]
  /** B6: ob Releases ueber die Szene-Nummerierung gelesen werden, wie Sonarrs `useSceneNumbering`. */
  use_scene_numbering: boolean
  /** B6: wie viele Folgen eine Szene-Nummer tragen; ohne welche aendert der Schalter nichts. */
  scene_numbers: number
  /** `mapped`, `not_listed`, `bridge_failed`, `no_tvdb` oder ein Fehlercode wie `xem_timeout`. null vor der ersten Frage. */
  xem_state: string | null
  xem_checked_at: string | null
  numbering_note: Record<string, unknown> | null
}

/** Eine Korrektur, wie sie hinausgeht. */
export type NumberingCorrectionIn = { episode_id: number; season?: number | null; episode?: number | null; episode_end?: number | null; absolute?: number | null }

/** `PUT /api/series/{id}/numbering`. Was fehlt, bleibt. `episode_group_id` null heisst: wie TMDB. Listen ersetzen alles. */
export type SeriesNumberingUpdate = {
  episode_group_id?: string | null
  aliases?: string[]
  corrections?: NumberingCorrectionIn[]
  use_scene_numbering?: boolean
}

/** `GET` und `PUT /api/settings/xem`. */
export type XemSettings = { enabled: boolean; last_ok_at: string | null; last_error_code: string | null }

/* ------------------------------------------------------------------------------------------ */
/* Musik M1, wie in the design notes unter "M1.4" und "M1.5": Kuenstler suchen, Vorschau, */
/* hinzufuegen, die Kuenstlerseite, das Laden im Hintergrund, die Zielausgabe.                   */
/* ------------------------------------------------------------------------------------------ */

/** Die Gruppen einer Kuenstlerseite, in dieser Reihenfolge (Entscheidung 26). */
export type AlbumGroupName = 'studio' | 'ep' | 'single' | 'live' | 'compilation' | 'soundtrack' | 'remix' | 'spoken' | 'other'

/** `versions.track_counts`: `wanted` aus der Zielausgabe, `present` mit Datei (`services/music/store.py:_count_tracks`). */
export type TrackCounts = { wanted: number; present: number }

/** `GET /api/music/search/artists`. */
export type ArtistHit = {
  mbid: string
  name: string
  disambiguation: string | null
  artist_type: string | null
  country: string | null
  begin_year: number | null
  end_year: number | null
  score: number | null
  /** Die Nummer des Kuenstlers, wenn er schon in der Bibliothek steht. */
  in_library: number | null
}

export type GroupCount = { group: AlbumGroupName | string; count: number }

/** `POST /api/music/artists/preview`. */
export type ArtistPreview = {
  mbid: string
  name: string
  disambiguation: string | null
  artist_type: string | null
  country: string | null
  begin_year: number | null
  end_year: number | null
  groups_total: number
  pages: number
  groups: GroupCount[]
  /** Wie viele Alben die Wahl "Studioalben und EPs" ueberwachen wuerde. */
  would_watch: number
  in_library: number | null
}

/** Die Wahl beim Hinzufuegen eines Kuenstlers (Entscheidung 32). */
/** Seit V5 (Gabel 1) jede Wahl, die Lidarr beim Hinzufuegen kennt. */
export type ArtistMonitor = 'studio' | 'future' | 'none' | 'missing' | 'existing' | 'first' | 'latest'

/** `POST /api/music/artists`. `types`: die Gruppen der Kuenstlerseite, die zaehlen; ohne: Studioalben und EPs. */
export type ArtistIn = { mbid: string; monitor: ArtistMonitor; types?: string[] }

/** `GET /api/music/search/albums`. */
export type AlbumSearchKind = 'album' | 'compilation' | 'single'

export type AlbumHit = {
  mbid: string
  title: string
  primary_type: string | null
  secondary_types: string[]
  first_release_date: string | null
  /** Der Credit als ein Text. */
  artist: string
  artist_mbids: string[]
  score: number | null
  /** Die Nummer des Titels, wenn das Album schon in der Bibliothek steht. */
  in_library: number | null
}

/** `POST /api/music/albums`. */
export type AlbumIn = { mbid: string }

export type AlbumCreated = { id: number; created: boolean }

/** Die Regel fuer neue Alben eines Kuenstlers (Entscheidung 24). */
export type ArtistMonitorNew = 'all' | 'none'

/** `PATCH /api/music/artists/{id}`. */
export type ArtistPatch = { monitor_new?: ArtistMonitorNew; monitored?: boolean }

/** `load_state` eines Kuenstlers (M1.2). */
export type ArtistLoadState = 'queued' | 'groups' | 'releases' | 'ready' | 'failed'

export type ArtistSummary = {
  id: number
  mbid: string
  name: string
  alias_display: string | null
  disambiguation: string | null
  artist_type: string | null
  country: string | null
  begin_year: number | null
  end_year: number | null
  is_various: boolean
  monitor_new: ArtistMonitorNew
  load_state: ArtistLoadState
  load_error: string | null
  load_done: number
  load_total: number | null
  groups_total: number | null
  /** Seit V5: false, solange der Kuenstler eingefroren ist (wie "nicht ueberwacht" in Lidarr). */
  monitored?: boolean
  /** Seit T1: seine Tags. */
  tags?: string[]
  /** Die Gruppen, deren neue Alben ueberwacht werden; null sind Studioalben und EPs. */
  album_types?: string[] | null
  /** Alben mit einer Fassung. */
  albums: number
  /** Alben, deren Fassung jeden Titel der Zielausgabe hat. */
  complete: number
  /** Alben ohne eine einzige Datei. Aeltere Server schicken die Zahlen unten nicht. */
  missing?: number
  /** Alben, denen Titel fehlen. */
  incomplete?: number
  upgrade?: number
  downloading?: number
  problem?: number
  /** Was die Alben dieses Kuenstlers auf der Platte belegen. */
  size_bytes?: number
  /** Der eine Zustand, den die Zeile zeigt; null, wenn alles da und gut ist. */
  state?: string | null
  /** Der Titel, dessen Cover die Kachel zeigt (Entscheidung 38). */
  cover_title_id: number | null
  cover_url: string | null
  mb_gone_at: string | null
}

export type ArtistList = { items: ArtistSummary[]; total: number }

/** `DELETE /api/music/artists/{id}` Antwort (Entscheidung 43). */
export type ArtistRemoved = { artist_id: number; albums_removed: number; versions_removed: number }

export type ArtistAlbum = {
  id: number
  mbid: string | null
  title: string
  year: number | null
  first_release_date: string | null
  primary_type: string | null
  secondary_types: string[]
  disambiguation: string | null
  group: AlbumGroupName | string
  cover_url: string | null
  version_id: number | null
  monitored: boolean
  state: VersionState | null
  track_counts: TrackCounts | null
  target_tracks: number | null
  mb_gone_at: string | null
}

export type ArtistGroup = { group: AlbumGroupName | string; open: boolean; albums: ArtistAlbum[] }

export type ArtistDetail = ArtistSummary & {
  groups: ArtistGroup[]
  groups_refreshed_at: string | null
  groups_due_at: string | null
}

export type LoadingCurrent = { artist_id: number; name: string; step: string; done: number; total: number | null }

export type LoadingFailed = { artist_id: number; name: string; code: string | null }

/** `GET /api/music/loading`. */
export type LoadingStatus = {
  enabled: boolean
  queued: number
  current: LoadingCurrent | null
  failed: LoadingFailed[]
  /** Alben, deren Ausgaben nicht luden, weil MusicBrainz ausgelastet blieb; der Kuenstler ist trotzdem fertig. */
  failed_albums: number
  requests_last_hour: number
  busy_last_hour: number
}

/** `PATCH /api/music/albums/{title_id}/target`. */
export type TargetIn = { release_id: number | null }

/** `GET`/`PUT /api/music/settings/musicbrainz`. */
export type WatchOut = { title_id: number; version_id: number | null; monitored: boolean; state: VersionState | null }
export type CountriesOut = { countries: string[] | null; default: string[] }
export type MusicBrainzState = { enabled: boolean; covers_enabled: boolean; last_ok_at: string | null; last_error_code: string | null }

export type MusicBrainzIn = { enabled?: boolean; covers_enabled?: boolean }

export type ReleaseTrackOut = {
  id: number
  medium: number
  position: number
  number: string | null
  name: string
  length_ms: number | null
  artist_credit: { name?: string; mbid?: string; join?: string }[] | null
}

export type ReleaseMediumOut = { position: number; format: string | null; name: string | null; track_count: number; tracks: ReleaseTrackOut[] }

/** `GET /api/music/releases/{id}`. */
export type ReleaseDetail = {
  id: number
  mbid: string
  name: string
  date: string | null
  country: string | null
  formats: string[]
  media_count: number
  track_count: number
  labels: { name?: string; catalog_number?: string }[]
  disambiguation: string | null
  tracks_loaded: boolean
  media: ReleaseMediumOut[]
}

/** Eine Ausgabe ohne Titelliste, wie sie an der Fassung und in `AlbumBlock.releases` steht. */
export type ReleaseHead = {
  id: number
  mbid: string
  name: string
  date: string | null
  country: string | null
  formats: string[]
  media_count: number
  track_count: number
  labels: { name?: string; catalog_number?: string }[]
  disambiguation: string | null
  audio_only: boolean
  tracks_loaded: boolean
  gone: boolean
}

/** `PATCH /api/music/albums/{title_id}/target` Antwort. */
export type TargetOut = {
  version_id: number
  target_release_id: number | null
  target_set_by: string | null
  target_reason: TargetReason | null
  target_suggestion_id: number | null
  track_counts: TrackCounts | null
}

/** Ein Grund der Zielregel (Entscheidung 27), als Kennung mit Werten. Nie ein Satz, den baut die Oberflaeche. */
export type TargetReasonCode = { code: string; [value: string]: unknown }

export type TargetReason = { codes: TargetReasonCode[] }

export type AlbumCredit = { mbid: string; name: string; join: string; artist_id: number | null }

export type AlbumTrack = {
  id: number
  medium: number
  medium_format: string | null
  position: number
  number: string | null
  name: string
  length_ms: number | null
  artist_credit: { name?: string; mbid?: string; join?: string }[] | null
  present: boolean
  /** Seit M4 `tags_state`: woher die Tags der Datei stammen (download, written, linked, format, off, failed). */
  file: {
    id?: number
    relative_path?: string
    size?: number
    quality?: string
    tags_state?: string | null
    /** Seit dem 19.09.2026: woher die Datei kam, `download` (dann die folgenden Felder) oder `lidarr`. */
    source?: 'download' | 'lidarr' | null
    download_id?: number
    download_release?: string | null
    download_path?: string
    via?: string | null
    filed_at?: string | null
  } | null
}

/** `TitleDetail.album`, nur bei `kind = "album"`. */
export type AlbumBlock = {
  mbid: string | null
  artist_id: number | null
  artist_name: string | null
  credit: AlbumCredit[]
  primary_type: string | null
  secondary_types: string[]
  group: AlbumGroupName | string
  first_release_date: string | null
  disambiguation: string | null
  releases_state: 'none' | 'heads' | 'tracks' | 'failed' | string
  releases_refreshed_at: string | null
  mb_gone_at: string | null
  version_id: number | null
  monitored: boolean
  target: ReleaseHead | null
  target_set_by: string | null
  target_reason: TargetReason | null
  suggestion: ReleaseHead | null
  actual: ReleaseHead | null
  track_counts: TrackCounts | null
  tracks: AlbumTrack[]
  releases: ReleaseHead[]
  /** Musik M6: Dateien im Albumordner ohne Titel, die der Besitzer noch nicht geklaert hat. */
  unclear_files?: AlbumUnclearFile[]
  /** Musik M6: ein eigenes Album mit Ordner, das neu eingelesen werden kann. */
  can_read_folder?: boolean
  files_read_at?: string | null
}

/** Musik M6: eine unklare Datei, Pfad relativ zum Albumordner. */
export type AlbumUnclearFile = {
  id: number
  relative_path: string
  size: number | null
  quality: string | null
}

/** Musik M2: die fuenf Qualitaetsstufen, beste zuerst, und was das Profil mit jeder tut. */
export type MusicStep = 'lossless_24' | 'lossless' | 'lossy_high' | 'lossy_mid' | 'lossy_low'
export type MusicStepRole = 'target' | 'for_now' | 'waits' | 'never'

export type MusicProfileSummary = {
  quality: 'lossless' | 'either' | 'lossy'
  take_now: boolean
  hires: 'any' | 'prefer' | 'avoid'
  source: 'avoid_vinyl' | 'any' | 'prefer_cd'
  ladder: { step: MusicStep; role: MusicStepRole }[]
}

export type MusicProfile = { version_id: number; answers: ProfileAnswers; summary: MusicProfileSummary; updated_at: string }
export type MusicProfilePreview = { answers: ProfileAnswers; summary: MusicProfileSummary }

/** Was nexcrate im Namen eines Musik-Releases liest. Kuenstler und Album sind nur ein Vorschlag. */
export type ParsedAlbum = {
  release_title: string
  artist: string
  album: string
  unsure: boolean
  various_artists: boolean
  year: number | null
  edition_year: number | null
  format: string | null
  format_assumed: boolean
  bitrate: string | null
  bit_depth: number | null
  sample_rate_khz: number | null
  source: string | null
  media_count: number | null
  disc_part: number | null
  editions: string[]
  kinds: string[]
  several_albums: boolean
  country: string | null
  catalogue_number: string | null
  group: string | null
  suffix: string | null
  revision: { version: number; real: number; repack: boolean }
  warnings: string[]
  shape: string
}

export type AlbumNote = { code: string; [value: string]: unknown }

export type AlbumVerdict = {
  accepted: boolean
  for_now: boolean
  rejections: ReleaseRejection[]
  notes: AlbumNote[]
  place: number | null
}

export type AlbumCheckRelease = { name: string; parsed: ParsedAlbum; step: MusicStep | 'unknown'; verdict: AlbumVerdict | null }
export type AlbumCheck = { has_profile: boolean; releases: AlbumCheckRelease[] }

/*
 * Musik M3: die Suche nach einem Album. Eigene Routen: starten mit
 * `POST /api/music/albums/{id}/search`, lesen mit `GET /api/music/searches/{search_id}`. Geladen wird nichts.
 */

/** this: das gesuchte Album. other_album: ein anderes des Kuenstlers. unknown_album, other_artist, not_an_album. */
export type AlbumMatchKind = 'this' | 'other_album' | 'unknown_album' | 'other_artist' | 'not_an_album' | string

export type AlbumMatch = {
  kind: AlbumMatchKind
  title_id: number | null
  album: string | null
  year: number | null
  /** Bei not_an_album: several_albums, tribute, karaoke, sampler, not_a_sampler. */
  reason: string | null
  read_artist: string
  read_album: string
  /** Der Name sagt single, ep, live und so weiter, das Album ist es nicht. */
  kind_differs: string | null
}

export type AlbumSearchVerdict = {
  /** false ohne Musik-Profil: dann lehnt nexcrate nur ab, was es nie nimmt. */
  judged: boolean
  accepted: boolean
  for_now: boolean
  rejections: ReleaseRejection[]
  notes: AlbumNote[]
  rank: number[]
}

export type AlbumSearchRelease = {
  release_key: string
  indexer_id: number
  indexer_name: string
  protocol: 'usenet' | 'torrent' | string
  title: string
  size_bytes: number | null
  published_at: string | null
  age_hours: number | null
  seeders: number | null
  peers: number | null
  grabs: number | null
  categories: number[]
  priority: number
  match: AlbumMatch
  step: MusicStep | null
  format: string | null
  bit_depth: number | null
  source: string | null
  /** Nur fuer Releases des gesuchten Albums. */
  verdict: AlbumSearchVerdict | null
  place: number | null
  /** Seit M4, nur fuer Releases dieses Albums: ob die Musik-Fassung es laden kann, und warum nicht. */
  can_load?: boolean | null
  load_block?: string | null
  /** Bis wann die Automatik dieses Release warten liesse; null: sie wuerde sofort laden. */
  would_wait_until?: string | null
  blocklisted?: boolean | null
}

export type AlbumSearchDecision = {
  version_id: number | null
  label: string
  has_profile: boolean
  would_take: string | null
  keeps_current: boolean
  nothing_fits: { code: string; count: number }[]
  /** Seit M4: warum die Musik-Fassung gar nicht laden kann; null, wenn sie kann. */
  load_block?: string | null
}

export type AlbumSearch = {
  search_id: string
  title_id: number
  state: SearchState
  scope: { kind: 'album'; aliases: boolean }
  started_at: string
  finished_at: string | null
  indexers: SearchIndexer[]
  versions: AlbumSearchDecision[]
  releases: AlbumSearchRelease[]
}

export type AlbumSearchStartBody = { aliases?: boolean }

/* ----------------------------------------------------------------------------------------- */
/* Expertenmodus, wie in the design notes: Qualitaeten, eigene Formate, Profil.    */
/* ----------------------------------------------------------------------------------------- */

/**
 * Eine Qualitaet mit ihren Grenzen, in MB je Minute wie in Radarr. `null` heisst ohne Obergrenze. Der Wunschwert
 * entscheidet nur zwischen Releases, die sonst gleich sind: das naechste daran gewinnt, ohne ihn das groessere.
 */
export type QualitySizeRow = { quality: string; min_mb_per_min: number; max_mb_per_min: number | null; preferred_mb_per_min: number | null }

export type QualityList = { kind: MediaKind; items: QualitySizeRow[] }

/** Eine Bedingung in Radarrs Form. `fields` haelt je nach Art `value` oder `min` und `max`. */
export type FormatSpecification = {
  implementation: string
  negate?: boolean
  required?: boolean
  fields?: Record<string, unknown>
  [more: string]: unknown
}

export type CustomFormat = {
  id: number
  kind: MediaKind
  name: string
  /** `trash` kam mit den Leitfaeden, `own` gehoert dem Besitzer. */
  origin: string
  trash_id: string | null
  specifications: FormatSpecification[]
  /** Die Namen der Fassungen, deren Profil darauf zeigt. */
  used_by: string[]
  updated_at: string
}

export type ConditionOption = { value: number; label: string }

/** Was eine Bedingung vergleichen kann: ein Muster, eine Auswahl oder ein Bereich. */
export type FormatCondition = {
  implementation: string
  value: 'regex' | 'choice' | 'range'
  options: ConditionOption[]
  unit: string | null
  except_language: boolean
}

export type FormatConditionList = { kind: MediaKind; items: FormatCondition[] }

/** Was der Test zu einer Bedingung sagt. `result` gilt nach dem Umkehren, `index` ist ihr Platz in der Liste. */
export type FormatTestCondition = { index: number; implementation: string; result: boolean; required: boolean; problem: 'pattern_invalid' | null }

/** Ein Format gegen einen Release-Namen: das Urteil, jede Bedingung, jede Gruppe, und als was der Name gelesen wurde. */
export type FormatTestResult = {
  matches: boolean
  conditions: FormatTestCondition[]
  groups: { implementation: string; matches: boolean }[]
  parsed: { quality?: string; group?: string | null; languages?: string[]; [more: string]: unknown }
}

/** Ein Verweis auf ein Format mit seinen Punkten. */
export type ExpertFormatEntry = { format_id: number; score: number }

/**
 * Ein Eintrag der Qualitaetenliste, wie die Regeln ihn halten: eine einzelne Qualitaet mit `name` oder eine
 * Gruppe gleichwertiger mit `group` und `items`. `allowed` sagt, ob sie genommen wird.
 */
export type ProfileQualityEntry = { name?: string; group?: string; items?: string[]; allowed: boolean }

/** Das Profil, wie es von Hand gesetzt wird. `qualities` und `cutoff` haben die Form der Regeln. */
export type ExpertProfileBody = {
  qualities: ProfileQualityEntry[]
  cutoff: string | null
  upgrades_allowed: boolean
  min_score: number
  upgrade_until: number
  min_upgrade_step: number
  target_resolution: number | null
  languages: LanguageEntry[]
  required_languages: string
  formats: ExpertFormatEntry[]
  /**
   * Nur bei Serien (Anime B2): die Regeln, nach denen eine Anime-Serie dieser Fassung beurteilt wird. Fehlt er oder
   * ist er null, zaehlt fuer eine Anime-Serie das Profil selbst, wie fuer jede andere Serie.
   */
  anime?: ExpertProfileBody | null
}

/** Welches der beiden Anime-Profile von TRaSH; ohne Angabe entscheidet Deutsch unter den Sprachen des Profils. */
export type AnimeTrashFamily = 'standard' | 'german'

/** Anime B2b: der Anime-Zweig, wie TRaSHs Anime-Profil ihn haette. Noch nicht gespeichert. */
export type AnimeFromTrash = {
  anime: ExpertProfileBody
  /** Die TRaSH-Datei, aus der er kommt, etwa `anime-remux-1080p`. */
  trash_profile: string
  /** Formate, die dafuer neu angelegt wurden. */
  formats_created: number
  /** Alle Formate der Art, die neuen eingeschlossen. */
  formats_available: CustomFormat[]
}

/** Anime B5: was TMDB fuer eine Serie vorschlaegt, gegen die Art, die sie hat. */
export type SeriesTypeProposal = { title_id: number; title: string; year: number | null; series_type: string; proposed: string }

export type SeriesTypeRun = { state: 'idle' | 'running' | 'done' | string; done: number; total: number; failed: number; problem: string | null }

/** `GET /api/library/series-type/proposals`. `unknown`: Serien ohne Vorschlag, die der Lauf erst fragen muss. */
export type SeriesTypeProposals = { proposals: SeriesTypeProposal[]; unknown: number; run: SeriesTypeRun }

export type ExpertProfile = {
  /** Null, wenn das Profil fuer sich gelesen wurde. */
  version_id: number | null
  profile_id: number
  name: string
  kind: MediaKind
  /** `wizard`, solange der Assistent das Profil haelt, sonst `expert`. */
  mode: string
  expert: ExpertProfileBody
  /** Jede Qualitaet der Art, von der schwaechsten zur besten. */
  qualities_available: string[]
  formats_available: CustomFormat[]
  /** `original` und jeder Sprachcode, den die Bewertung kennt: was ein Release tragen muss, wie Radarrs Profil-Sprache. */
  languages_available: string[]
}

/** Was aus dem Qualitaets-Aufbau einer Radarr- oder Sonarr-Verbindung hier wuerde (Plan E5). */
export type ArrQualityProfile = {
  name: string
  /** Ein Profil dieses Namens gibt es hier schon; dieses bliebe weg. */
  taken: boolean
  qualities: number
  cutoff: string | null
  min_score: number
  upgrade_until: number
  upgrades_allowed: boolean
  scored_formats: number
  /** `qualities_unknown`, `language_unknown`, `nothing_allowed`, `repack_unscored`. */
  notes: string[]
}

export type ArrQualityFormat = {
  name: string
  conditions: number
  exists: boolean
  /** Bedingungsarten, fuer die nexcrate keinen Platz hat. */
  unknown_types: string[]
  /** Aus einem Release-Profil von Sonarr gebaut, dort kein Format. */
  from_release_profile: boolean
}

/** Ein Release-Profil von Sonarr und die Formate, die hier daraus werden. */
export type ArrReleaseProfile = {
  name: string
  enabled: boolean
  formats: string[]
  /** `indexer`, `tags`: dort gebunden, hier nicht abbildbar; die Formate kommen dann ohne Punkte. */
  bound: string[]
  unreadable_terms: string[]
}

export type ArrQualitySetup = {
  source_id: number
  app: string
  kind: MediaKind
  profiles: ArrQualityProfile[]
  formats: ArrQualityFormat[]
  sizes: number
  unknown_qualities: string[]
  release_profiles: ArrReleaseProfile[]
  /** Die Repack-Formate der Leitfaeden, nur solange ein Profil `repack_unscored` traegt. */
  repack_offer: { name: string; score: number }[]
  /** Die andere App nimmt Repacks von sich aus als Verbesserung (ihre Vorgabe). */
  repacks_upgrade_there: boolean
}

export type ArrQualityTaken = { formats: number; sizes: number; profiles: number }

// Medienserver -------------------------------------------------------------------

export type MediaServerKind = 'plex' | 'jellyfin' | 'emby'

/** Dieselbe Stelle, wie nexcrate sie sieht und wie der Medienserver sie sieht. */
export type MediaServerPathMapping = { local: string; remote: string }

export type MediaServerLibrary = {
  id: string
  name: string
  /** `movie`, `series`, `music` oder null fuer alles andere (Fotos, Gemischtes). */
  kind: 'movie' | 'series' | 'music' | null
  locations: string[]
  /** Ob nexcrate dieser Bibliothek Aenderungen meldet. */
  refresh: boolean
}

/** Den Token oder API-Schluessel gibt der Server nie heraus, nur `has_token`. */
export type MediaServer = {
  id: number
  name: string
  kind: MediaServerKind
  url: string
  has_token: boolean
  enabled: boolean
  server_name: string | null
  version: string | null
  libraries: MediaServerLibrary[]
  path_mappings: MediaServerPathMapping[]
  /** Letzter Fehler, oder der Zustand `mediaserver_path_unmatched`; null, wenn alles gut ist. */
  last_error_code: string | null
  last_checked_at: string | null
  last_notify_at: string | null
  last_notify_result: 'folder' | 'library' | 'failed' | null
}

export type MediaServerCreate = {
  name: string
  kind: MediaServerKind
  url: string
  token?: string
  /** Nur Plex: die bestaetigte Anmeldung bei plex.tv. Der Token selbst bleibt im Server von nexcrate. */
  plex_pin_id?: number
  enabled?: boolean
  path_mappings?: MediaServerPathMapping[]
}

/** Jedes Feld darf fehlen. Ohne `token` bleibt der gespeicherte. `libraries` stellt nur die Schalter. */
export type MediaServerUpdate = Partial<Omit<MediaServerCreate, 'kind'>> & { libraries?: { id: string; refresh: boolean }[] }

export type MediaServerTest = { id?: number; kind?: MediaServerKind; url?: string; token?: string; plex_pin_id?: number }

/** Ein Pfadpaar, das der Dialog anbietet. Ein Vorschlag: gespeichert wird erst, was der Besitzer speichert. */
export type MediaServerSuggestedMapping = MediaServerPathMapping & { reason: 'same_ending' | 'only_one' }

export type MediaServerTestResult = {
  server_name: string
  version: string
  libraries: MediaServerLibrary[]
  /** Aus den Ordnern des Servers und den Ordnern der Fassungen. Ein Server von davor schickt sie nicht. */
  suggested_mappings?: MediaServerSuggestedMapping[]
  /** false: der Server sieht die Ordner der Fassungen unter demselben Pfad, es braucht kein Paar. */
  mappings_needed?: boolean
}

/** Ein eigener Plex-Server des angemeldeten Kontos, wie plex.tv ihn nennt. Nie ein Token. */
export type PlexServer = { machine_id: string; name: string; addresses: string[] }

export type PlexServers = { servers: PlexServer[]; shared_hidden: number }

/** Der gewaehlte Server: die erste Adresse, unter der er selbst geantwortet hat, und was eine Pruefung liefert. */
export type PlexChosen = MediaServerTestResult & { name: string; url: string }

export type PlexPin = { pin_id: number; auth_url: string; expires_in: number }

export type PlexPinState = { state: 'waiting' | 'claimed' | 'expired' }

/** Was ein Schluessel fuer `/api/v1` darf. `read` ist immer dabei. */
export type ApiKeyScope = 'read' | 'request' | 'operate'

/** Ein Schluessel fuer andere Programme. Der Schluessel selbst steht nie darin, nur seine letzten vier Zeichen. */
export type ApiKey = {
  id: number
  name: string
  hint: string
  /** Als Text: ein Recht, das diese Oberflaeche noch nicht kennt, darf sie nicht brechen. */
  scopes: string[]
  created_at: string
  last_used_at: string | null
}

/** Die Antwort auf das Anlegen: das einzige Mal, dass `key` die App verlaesst. */
export type ApiKeyCreated = ApiKey & { key: string }

export type ApiKeyList = { items: ApiKey[]; scopes: string[] }

/** Eine Regel fuer Mediatheken. `genre` englisch wie TMDB, `tag` Kennungen. */
export type FolderRuleWhen = 'genre' | 'certification' | 'tag' | 'series_type'
export type FolderRule = { when: FolderRuleWhen; values: string[]; folder: string }
/** Ein Titel, den die Regeln woanders haetten (`GET /api/versions/{id}/relocations`). */
export type Relocation = { title_id: number; name: string; year: number | null; from: string; to: string; folder: string; skip: string | null }

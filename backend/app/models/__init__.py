"""All model modules.

⚠️ ``create_all`` only knows tables whose module has been imported. A new model
module is imported here, or its tables are never created.
"""

from .api import API_SCOPES, ApiDownloadMark, ApiEvent, ApiKey, ApiPairing, TitleChange, Webhook, WebhookDelivery
from .base import Base, UTCDateTime, utcnow
from .core import ACCOUNT_ID, Account, Session, Setting, TmdbCacheEntry
from .disk import DiskFolder, DiskRoot
from .downloads import (
    BlocklistEntry,
    Download,
    DownloadAudioFile,
    DownloadClient,
    DownloadEpisode,
    DownloadFile,
    ExtraFile,
    ForeignJob,
    PendingRelease,
)
from .media import (
    AlternateTitle,
    CustomFormat,
    HistoryEntry,
    ImportRun,
    Indexer,
    Profile,
    QualitySize,
    Source,
    Title,
    Version,
    VersionDefinition,
)
from .mediaserver import MediaServer
from .music import AlbumArtist, Artist, Release, ReleaseMedium, ReleaseTrack, SourceUnmappedFile, TrackFile
from .notify import NotificationMessage, NotificationTarget
from .recycle import RecycleEntry
from .rename import RenameRun, RenameStep, RenameTitle
from .series import (
    Episode,
    EpisodeFile,
    EpisodeNumber,
    EpisodeVersion,
    Season,
    SeasonFolder,
    SeasonVersion,
    SourceEpisode,
    TitleAlias,
    XemName,
)
from .tags import ArtistTag, AutoTag, DownloadClientTag, IndexerTag, Tag, TitleTag

__all__ = [
    "ACCOUNT_ID",
    "API_SCOPES",
    "Account",
    "AlbumArtist",
    "AlternateTitle",
    "ApiDownloadMark",
    "ApiEvent",
    "ApiKey",
    "ApiPairing",
    "Artist",
    "ArtistTag",
    "AutoTag",
    "Base",
    "BlocklistEntry",
    "CustomFormat",
    "DiskFolder",
    "DiskRoot",
    "Download",
    "DownloadAudioFile",
    "DownloadClient",
    "DownloadClientTag",
    "DownloadEpisode",
    "DownloadFile",
    "Episode",
    "EpisodeFile",
    "EpisodeNumber",
    "EpisodeVersion",
    "ExtraFile",
    "ForeignJob",
    "HistoryEntry",
    "ImportRun",
    "Indexer",
    "IndexerTag",
    "MediaServer",
    "NotificationMessage",
    "NotificationTarget",
    "PendingRelease",
    "Profile",
    "QualitySize",
    "RecycleEntry",
    "Release",
    "ReleaseMedium",
    "ReleaseTrack",
    "RenameRun",
    "RenameStep",
    "RenameTitle",
    "Season",
    "SeasonFolder",
    "SeasonVersion",
    "Session",
    "Setting",
    "Source",
    "SourceEpisode",
    "SourceUnmappedFile",
    "Tag",
    "Title",
    "TitleAlias",
    "TitleChange",
    "TitleTag",
    "TmdbCacheEntry",
    "TrackFile",
    "UTCDateTime",
    "Version",
    "VersionDefinition",
    "Webhook",
    "WebhookDelivery",
    "XemName",
    "utcnow",
]

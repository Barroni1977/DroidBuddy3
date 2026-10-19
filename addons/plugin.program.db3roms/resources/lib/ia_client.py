"""Pure-Python Internet Archive helpers used by DB3 ROM Browser."""

from html import unescape
from html.parser import HTMLParser
from urllib.parse import quote, urlencode


SEARCH_ENDPOINT = "https://archive.org/advancedsearch.php"
METADATA_ENDPOINT = "https://archive.org/metadata/"
DOWNLOAD_ENDPOINT = "https://archive.org/download/"
THUMB_ENDPOINT = "https://archive.org/services/img/"
DETAIL_ENDPOINT = "https://archive.org/details/"

DEFAULT_EXTENSIONS = (
    ".zip", ".7z", ".nes", ".sfc", ".smc", ".gba", ".gb", ".gbc",
    ".n64", ".z64", ".v64", ".md", ".gen", ".sms", ".gg", ".pce",
    ".cue", ".chd", ".iso", ".bin", ".wad", ".a26", ".a52", ".a78",
    ".lnx", ".ngp", ".ngc", ".ws", ".wsc", ".col", ".int", ".rom",
)

SEARCH_FIELDS = (
    "identifier", "title", "description", "date", "year", "downloads",
    "creator", "subject", "licenseurl",
)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        value = data.strip()
        if value:
            self.parts.append(value)


class _HiddenInputExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "input":
            return
        values = {str(key).lower(): value for key, value in attrs}
        if str(values.get("type") or "").lower() != "hidden":
            return
        name = values.get("name")
        if name:
            self.values[str(name)] = str(values.get("value") or "")


def hidden_form_fields(html):
    parser = _HiddenInputExtractor()
    try:
        parser.feed(str(html or ""))
    except Exception:
        return {}
    return parser.values


def strip_html(value):
    if isinstance(value, list):
        value = " ".join(str(part) for part in value)
    if not value:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(unescape(str(value)))
        return " ".join(parser.parts)
    except Exception:
        return str(value)


def _lucene_phrase(value):
    value = str(value or "").replace("\\", "\\\\").replace('"', '\\"').strip()
    return '"{}"'.format(value)


def homebrew_query():
    return 'mediatype:software AND subject:homebrew'


def public_domain_query():
    return 'mediatype:software AND subject:"public domain"'


def user_search_query(term):
    return "mediatype:software AND {}".format(_lucene_phrase(term))


def build_search_url(query, page=1, rows=25):
    pairs = [("q", query)]
    pairs.extend(("fl[]", field) for field in SEARCH_FIELDS)
    pairs.extend(
        (
            ("rows", str(max(1, int(rows)))),
            ("page", str(max(1, int(page)))),
            ("sort[]", "downloads desc"),
            ("output", "json"),
        )
    )
    return "{}?{}".format(SEARCH_ENDPOINT, urlencode(pairs))


def metadata_url(identifier):
    return METADATA_ENDPOINT + quote(str(identifier), safe="")


def thumbnail_url(identifier):
    return THUMB_ENDPOINT + quote(str(identifier), safe="")


def details_url(identifier):
    return DETAIL_ENDPOINT + quote(str(identifier), safe="")


def download_url(identifier, filename):
    return "{}{}/{}".format(
        DOWNLOAD_ENDPOINT,
        quote(str(identifier), safe=""),
        quote(str(filename), safe="/"),
    )


def parse_extensions(value):
    entries = []
    for raw in str(value or "").split(","):
        item = raw.strip().lower()
        if not item:
            continue
        if not item.startswith("."):
            item = "." + item
        if item not in entries:
            entries.append(item)
    return tuple(entries) or DEFAULT_EXTENSIONS


def filter_rom_files(
    files, extensions=DEFAULT_EXTENSIONS, show_all=False, allow_private=False
):
    extensions = tuple(ext.lower() for ext in extensions)
    output = []
    for entry in files or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("private") and not allow_private:
            continue
        name = str(entry.get("name") or "").strip()
        if not name or name.endswith("/"):
            continue
        lower = name.lower()
        if any(marker in lower for marker in (
            "__ia_thumb", "_meta.", "_files.xml", "_reviews.xml",
            "_archive.torrent", "_meta.sqlite", "_thumb.jpg",
        )):
            continue
        if not show_all and not lower.endswith(extensions):
            continue
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        output.append(
            {
                "name": name,
                "size": size,
                "format": str(entry.get("format") or ""),
                "source": str(entry.get("source") or ""),
                "md5": str(entry.get("md5") or ""),
            }
        )
    return sorted(output, key=lambda item: item["name"].lower())


def format_size(size):
    try:
        value = float(size)
    except (TypeError, ValueError):
        return "Unknown size"
    units = ("B", "KB", "MB", "GB", "TB")
    unit = units[0]
    for candidate in units:
        unit = candidate
        if value < 1024.0 or candidate == units[-1]:
            break
        value /= 1024.0
    if unit == "B":
        return "{} {}".format(int(value), unit)
    return "{:.1f} {}".format(value, unit)


def normalise_document(document):
    document = document or {}
    title = document.get("title") or document.get("identifier") or "Untitled item"
    if isinstance(title, list):
        title = title[0] if title else "Untitled item"
    creator = document.get("creator") or ""
    if isinstance(creator, list):
        creator = ", ".join(str(value) for value in creator)
    year = document.get("year") or document.get("date") or ""
    if isinstance(year, list):
        year = year[0] if year else ""
    return {
        "identifier": str(document.get("identifier") or ""),
        "title": str(title),
        "description": strip_html(document.get("description")),
        "creator": str(creator),
        "year": str(year)[:4],
        "downloads": int(document.get("downloads") or 0),
        "license": str(document.get("licenseurl") or ""),
    }

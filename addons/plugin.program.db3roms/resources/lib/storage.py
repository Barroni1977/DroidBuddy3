"""Storage, folder and archive path helpers for DB3 ROM Browser."""

import os
import re


DEFAULT_SYSTEM_FOLDERS = (
    "Atari",
    "Game Boy",
    "Game Boy Advance",
    "MAME",
    "Mega Drive",
    "Nintendo 64",
    "Nintendo DS",
    "PlayStation",
    "PlayStation 2",
    "PSP",
    "SNES",
    "Other",
)

ARCHIVE_EXTENSIONS = (".zip", ".rar", ".7z")

GAME_EXTENSIONS = (
    ".zip", ".rar", ".7z", ".iso", ".cso", ".pbp", ".chd", ".cue",
    ".m3u", ".bin", ".rom", ".nes", ".fds", ".sfc", ".smc", ".gba",
    ".gb", ".gbc", ".n64", ".z64", ".v64", ".nds", ".md", ".gen",
    ".smd", ".sms", ".gg", ".32x", ".pce", ".wad", ".a26", ".a52",
    ".a78", ".lnx", ".ngp", ".ngc", ".ws", ".wsc", ".col", ".int",
)

SYSTEM_HINTS = (
    ("PlayStation 2", ("playstation 2", "ps2")),
    ("PlayStation", ("playstation", "psx", "ps1")),
    ("PSP", ("psp", "playstation portable")),
    ("Nintendo 64", ("nintendo 64", "n64")),
    ("Nintendo DS", ("nintendo ds", "nds")),
    ("Game Boy Advance", ("game boy advance", "gba")),
    ("Game Boy", ("game boy color", "game boy", "gbc")),
    ("SNES", ("super nintendo", "snes", "super famicom")),
    ("Mega Drive", ("mega drive", "genesis")),
    ("MAME", ("mame", "arcade")),
    ("Atari", ("atari",)),
)


def safe_component(value, fallback="New folder"):
    value = os.path.basename(str(value or "").replace("\\", "/"))
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value or fallback


def infer_system(*values):
    text = " ".join(str(value or "") for value in values).lower()
    for system, hints in SYSTEM_HINTS:
        if any(re.search(r"(?:^|[^a-z0-9]){}(?:$|[^a-z0-9])".format(re.escape(hint)), text)
               for hint in hints):
            return system
    return "Other"


def archive_type(filename):
    lower = str(filename or "").lower()
    for extension in ARCHIVE_EXTENSIONS:
        if lower.endswith(extension):
            return extension[1:]
    return ""


def is_game_file(filename):
    name = str(filename or "").lower()
    return bool(name and name.endswith(GAME_EXTENSIONS))


def archive_folder_name(filename):
    name = safe_component(filename, "Extracted game")
    for extension in ARCHIVE_EXTENSIONS:
        if name.lower().endswith(extension):
            name = name[:-len(extension)]
            break
    return safe_component(name, "Extracted game")


def is_within(root, path):
    try:
        root = os.path.realpath(os.path.abspath(root))
        path = os.path.realpath(os.path.abspath(path))
        return os.path.commonpath((root, path)) == root and path != root
    except (OSError, ValueError):
        return False


def safe_archive_member(name):
    value = str(name or "").replace("\\", "/")
    if not value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        return ""
    parts = []
    for part in value.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return ""
        clean = safe_component(part, "")
        if not clean:
            return ""
        parts.append(clean)
    return "/".join(parts)

"""Kodi entry point for DB3 ROM Browser."""

import json
import os
import re
import shutil
import stat
import sys
import time
import zipfile
from http.cookiejar import MozillaCookieJar
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from resources.lib import ia_client, storage


ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_NAME = ADDON.getAddonInfo("name")
ADDON_ICON = ADDON.getAddonInfo("icon")
HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]
PARAMS = dict(parse_qsl(sys.argv[2][1:] if len(sys.argv) > 2 else ""))
USER_AGENT = "DB3-ROM-Browser/0.3.4 Kodi"
DB3_SKIN_ID = "skin.droidbuddy3"
DB3_LIST_VIEW = 55
DB3_WINDOW_ID = 1199
IA_CSRF_URL = "https://archive.org/services/csrf-token"
IA_LOGIN_URL = "https://archive.org/services/account/login/"
IA_ACCOUNT_URL = "https://archive.org/account/index.php?output=json"
DATA_DIR = xbmcvfs.translatePath(
    "special://profile/addon_data/{}/".format(ADDON_ID)
)
HISTORY_PATH = DATA_DIR.rstrip("/\\") + "/history.json"
SESSION_PATH = DATA_DIR.rstrip("/\\") + "/ia-session-cookies.txt"
FOLDER_STATE_PATH = DATA_DIR.rstrip("/\\") + "/folders.json"


def log(message, level=xbmc.LOGINFO):
    xbmc.log("[{}] {}".format(ADDON_ID, message), level)


def plugin_url(**params):
    return "{}?{}".format(BASE_URL, urlencode(params))


def notify(message, error=False):
    icon = xbmcgui.NOTIFICATION_ERROR if error else xbmcgui.NOTIFICATION_INFO
    xbmcgui.Dialog().notification(ADDON_NAME, message, icon, 5000)


def load_session():
    jar = MozillaCookieJar(SESSION_PATH)
    if xbmcvfs.exists(SESSION_PATH):
        try:
            jar.load(ignore_discard=True, ignore_expires=False)
        except (OSError, ValueError):
            log("Internet Archive session could not be loaded.", xbmc.LOGWARNING)
    return jar


def session_is_signed_in(jar=None):
    if jar is None:
        jar = load_session()
    names = {
        cookie.name
        for cookie in jar
        if not cookie.is_expired()
    }
    return bool({"logged-in-user", "logged-in-sig"}.intersection(names))


def verify_account(opener):
    try:
        with opener.open(
            Request(
                IA_ACCOUNT_URL,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            ),
            timeout=25,
        ) as response:
            account = json.loads(response.read().decode("utf-8", "replace"))
        return bool(isinstance(account, dict) and account.get("username"))
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
        return False


def session_opener(jar=None):
    if jar is None:
        jar = load_session()
    return build_opener(HTTPCookieProcessor(jar)), jar


def update_account_status(signed_in=None):
    if signed_in is None:
        signed_in = session_is_signed_in()
    status = "signed-in" if signed_in else "signed-out"
    ADDON.setSetting("ia_account_status", "Signed in" if signed_in else "Signed out")
    try:
        xbmcgui.Window(10000).setProperty("DB3AuthStatus", status)
    except RuntimeError:
        pass
    try:
        xbmcgui.Window(DB3_WINDOW_ID).setProperty("DB3AuthStatus", status)
    except RuntimeError:
        pass


def sign_in():
    username = xbmcgui.Dialog().input(
        "Internet Archive email or username",
        type=xbmcgui.INPUT_ALPHANUM,
    ).strip()
    if not username:
        return
    password = xbmcgui.Dialog().input(
        "Internet Archive password",
        type=xbmcgui.INPUT_ALPHANUM,
        option=xbmcgui.ALPHANUM_HIDE_INPUT,
    )
    if not password:
        return

    jar = MozillaCookieJar(SESSION_PATH)
    opener, _ = session_opener(jar)
    payload = None
    login_data = None
    result = {}
    try:
        with opener.open(
            Request(
                IA_CSRF_URL,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            ),
            timeout=25,
        ) as response:
            token_result = json.loads(response.read().decode("utf-8", "replace"))
        token = (token_result.get("value") or {}).get("token")
        if not token_result.get("success") or not token:
            raise ValueError("Internet Archive did not return a login token.")
        login_data = {
            "username": username,
            "password": password,
            "remember": "true",
            "t": token,
        }
        payload = json.dumps(login_data).encode("utf-8")
        with opener.open(
            Request(
                IA_LOGIN_URL,
                data=payload,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Content-Type": "application/json; charset=utf-8",
                    "X-Csrf-Token": token,
                    "Origin": "https://archive.org",
                    "Referer": "https://archive.org/account/login",
                },
            ),
            timeout=25,
        ) as response:
            result = json.loads(response.read().decode("utf-8", "replace"))
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        log("Internet Archive sign-in failed: {}".format(exc), xbmc.LOGWARNING)
        update_account_status()
        notify("Could not sign in to Internet Archive.", error=True)
        return
    finally:
        password = None
        payload = None
        login_data = None

    accepted = bool(isinstance(result, dict) and result.get("success"))
    if accepted and not session_is_signed_in(jar):
        accepted = verify_account(opener)

    if not accepted:
        update_account_status()
        notify("Sign-in failed. Check the email and password in a web browser.", error=True)
        return

    xbmcvfs.mkdirs(DATA_DIR)
    try:
        jar.save(ignore_discard=True, ignore_expires=False)
        try:
            os.chmod(SESSION_PATH, 0o600)
        except OSError:
            pass
    except OSError as exc:
        log("Internet Archive session could not be saved: {}".format(exc), xbmc.LOGERROR)
        update_account_status(False)
        notify("Signed in, but Kodi could not save the session.", error=True)
        return
    update_account_status(True)
    notify("Signed in to Internet Archive.")


def sign_out():
    if xbmcvfs.exists(SESSION_PATH):
        xbmcvfs.delete(SESSION_PATH)
    update_account_status(False)
    notify("Signed out of Internet Archive.")


def get_bool_setting(setting_id):
    try:
        return ADDON.getSettingBool(setting_id)
    except AttributeError:
        return ADDON.getSetting(setting_id).lower() == "true"


def page_size():
    try:
        return min(100, max(5, int(ADDON.getSetting("page_size") or "25")))
    except ValueError:
        return 25


def allowed_extensions():
    return ia_client.parse_extensions(ADDON.getSetting("allowed_extensions"))


def request_json(url):
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    opener, _ = session_opener()
    try:
        with opener.open(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except HTTPError as exc:
        log("HTTP {} for {}".format(exc.code, url), xbmc.LOGERROR)
        raise RuntimeError("Internet Archive returned HTTP {}.".format(exc.code))
    except URLError as exc:
        log("Network error for {}: {}".format(url, exc), xbmc.LOGERROR)
        raise RuntimeError("Could not connect to Internet Archive.")
    except (ValueError, json.JSONDecodeError) as exc:
        log("Invalid JSON from {}: {}".format(url, exc), xbmc.LOGERROR)
        raise RuntimeError("Internet Archive returned an invalid response.")


def add_folder(label, action, art=None, info=None, label2="", **params):
    item = xbmcgui.ListItem(label=label, label2=label2)
    item.setProperty("IsPlayable", "false")
    if art:
        item.setArt(art)
    else:
        item.setArt({"icon": ADDON_ICON, "thumb": ADDON_ICON})
    if info:
        item.setInfo("video", info)
    params["action"] = action
    xbmcplugin.addDirectoryItem(
        HANDLE, plugin_url(**params), item, isFolder=True
    )


def add_action(label, action, art=None, label2="", **params):
    item = xbmcgui.ListItem(label=label, label2=label2)
    item.setProperty("IsPlayable", "false")
    item.setArt(art or {"icon": ADDON_ICON, "thumb": ADDON_ICON})
    params["action"] = action
    xbmcplugin.addDirectoryItem(
        HANDLE, plugin_url(**params), item, isFolder=False
    )


def finish(succeeded=True, view_mode=None):
    xbmcplugin.endOfDirectory(
        HANDLE, succeeded=succeeded, cacheToDisc=False
    )
    if succeeded and view_mode is not None and xbmc.getSkinDir() == DB3_SKIN_ID:
        xbmc.executebuiltin("Container.SetViewMode({})".format(view_mode))


def show_root():
    xbmcplugin.setPluginCategory(HANDLE, ADDON_NAME)
    xbmcplugin.setContent(HANDLE, "files")
    add_folder(
        "Homebrew",
        "browse",
        query=ia_client.homebrew_query(),
        heading="Homebrew",
        page="1",
    )
    add_folder(
        "Public domain",
        "browse",
        query=ia_client.public_domain_query(),
        heading="Public domain",
        page="1",
    )
    add_folder("Search Internet Archive", "search")
    add_folder("Download history", "history")
    add_action("Settings", "settings")
    finish()
    if xbmc.getSkinDir() == DB3_SKIN_ID:
        update_account_status()
        xbmc.executebuiltin("ReplaceWindow(1199)")


def search():
    term = xbmcgui.Dialog().input(
        "Search Internet Archive", type=xbmcgui.INPUT_ALPHANUM
    ).strip()
    if not term:
        finish()
        xbmc.executebuiltin("Action(Back)")
        return
    target = plugin_url(action="search_results", term=term, page="1")
    finish()
    xbmc.executebuiltin("Container.Update({},replace)".format(target))


def show_results(query, page, heading):
    page = max(1, int(page))
    rows = page_size()
    xbmcplugin.setPluginCategory(HANDLE, heading)
    xbmcplugin.setContent(HANDLE, "files")
    xbmc.executebuiltin("ActivateWindow(busydialognocancel)")
    try:
        payload = request_json(ia_client.build_search_url(query, page, rows))
    except RuntimeError as exc:
        notify(str(exc), error=True)
        finish(False)
        return
    finally:
        xbmc.executebuiltin("Dialog.Close(busydialognocancel)")

    response = payload.get("response") or {}
    documents = response.get("docs") or []
    total = int(response.get("numFound") or 0)
    for document in documents:
        entry = ia_client.normalise_document(document)
        if not entry["identifier"]:
            continue
        details = []
        if entry["year"]:
            details.append(entry["year"])
        if entry["downloads"]:
            details.append("{:,} downloads".format(entry["downloads"]))
        label2 = "  •  ".join(details)
        thumb = ia_client.thumbnail_url(entry["identifier"])
        info = {
            "title": entry["title"],
            "plot": entry["description"],
            "year": int(entry["year"]) if entry["year"].isdigit() else 0,
            "studio": entry["creator"],
        }
        add_folder(
            entry["title"],
            "files",
            art={"icon": thumb, "thumb": thumb, "poster": thumb},
            info=info,
            identifier=entry["identifier"],
            title=entry["title"],
            label2=label2,
        )

    if page * rows < total:
        add_folder(
            "[B]Next page  ›[/B]",
            "browse",
            query=query,
            heading=heading,
            page=str(page + 1),
        )
    if not documents:
        notify("No matching Internet Archive items were found.")
    finish(view_mode=DB3_LIST_VIEW)


def show_files(identifier, title):
    xbmcplugin.setPluginCategory(HANDLE, title or identifier)
    xbmcplugin.setContent(HANDLE, "files")
    xbmc.executebuiltin("ActivateWindow(busydialognocancel)")
    try:
        metadata = request_json(ia_client.metadata_url(identifier))
    except RuntimeError as exc:
        notify(str(exc), error=True)
        finish(False)
        return
    finally:
        xbmc.executebuiltin("Dialog.Close(busydialognocancel)")

    files = ia_client.filter_rom_files(
        metadata.get("files") or [],
        extensions=allowed_extensions(),
        show_all=True,
        allow_private=session_is_signed_in(),
    )
    thumb = ia_client.thumbnail_url(identifier)
    for entry in files:
        size_label = ia_client.format_size(entry["size"])
        format_label = entry["format"]
        label2 = "  •  ".join(
            value for value in (size_label, format_label) if value
        )
        add_action(
            entry["name"],
            "download",
            art={"icon": thumb, "thumb": thumb},
            label2=label2,
            identifier=identifier,
            filename=entry["name"],
            size=str(entry["size"]),
            title=title,
        )

    if not files:
        if session_is_signed_in():
            notify("No ROM files available to this signed-in account.")
        else:
            notify("No public direct ROM files found. The item may require sign-in.")
    finish(view_mode=DB3_LIST_VIEW)


def safe_filename(filename):
    name = os.path.basename(str(filename).replace("\\", "/"))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "download.rom"


def download_folder():
    folder = ADDON.getSetting("download_folder").strip()
    if not folder:
        folder = "special://profile/addon_data/{}/downloads/".format(ADDON_ID)
    folder = xbmcvfs.translatePath(folder)
    if not xbmcvfs.exists(folder):
        xbmcvfs.mkdirs(folder)
    return folder


def join_vfs(folder, filename):
    return folder.rstrip("/\\") + "/" + filename


def path_exists(path):
    return bool(path and (xbmcvfs.exists(path) or os.path.exists(path)))


def load_json_file(path, fallback):
    if not xbmcvfs.exists(path):
        return fallback
    handle = None
    try:
        handle = xbmcvfs.File(path)
        content = handle.read()
        return json.loads(content) if content else fallback
    except Exception as exc:
        log("Could not load {}: {}".format(path, exc), xbmc.LOGWARNING)
        return fallback
    finally:
        if handle:
            handle.close()


def save_json_file(path, value):
    xbmcvfs.mkdirs(DATA_DIR)
    handle = None
    try:
        handle = xbmcvfs.File(path, "w")
        handle.write(json.dumps(value, ensure_ascii=False, indent=2))
        return True
    except Exception as exc:
        log("Could not save {}: {}".format(path, exc), xbmc.LOGWARNING)
        return False
    finally:
        if handle:
            handle.close()


def ensure_system_folders(force=False):
    root = download_folder()
    state = folder_state()
    if state.get("_standard_folders_ready") and not force:
        return root
    for name in storage.DEFAULT_SYSTEM_FOLDERS:
        folder = join_vfs(root, name)
        if not xbmcvfs.exists(folder):
            xbmcvfs.mkdirs(folder)
    state["_standard_folders_ready"] = True
    save_json_file(FOLDER_STATE_PATH, state)
    return root


def managed_folders():
    root = ensure_system_folders()
    directories, _ = xbmcvfs.listdir(root)
    names = sorted(
        (name.rstrip("/\\") for name in directories), key=str.lower
    )
    return root, [name for name in names if name and name not in (".", "..")]


def folder_state():
    value = load_json_file(FOLDER_STATE_PATH, {})
    return value if isinstance(value, dict) else {}


def remember_folder(system, folder):
    state = folder_state()
    state[str(system or "Other")] = folder
    save_json_file(FOLDER_STATE_PATH, state)


def create_managed_folder(root=None):
    root = root or download_folder()
    name = xbmcgui.Dialog().input(
        "New ROM folder name", type=xbmcgui.INPUT_ALPHANUM
    ).strip()
    if not name:
        return ""
    name = storage.safe_component(name, "")
    if not name:
        notify("That folder name is not valid.", error=True)
        return ""
    folder = join_vfs(root, name)
    if xbmcvfs.exists(folder):
        notify("That folder already exists.", error=True)
        return folder
    if not xbmcvfs.mkdirs(folder):
        notify("Kodi could not create that folder.", error=True)
        return ""
    notify("Created {}".format(name))
    return folder


def select_destination(system):
    root, names = managed_folders()
    paths = [root] + [join_vfs(root, name) for name in names]
    labels = ["ROM Library root"] + names + ["Create a new folder…"]
    preferred = folder_state().get(system) or join_vfs(root, system)
    try:
        preselect = paths.index(preferred)
    except ValueError:
        preselect = 0
    selected = xbmcgui.Dialog().select(
        "Choose destination for {}".format(system), labels, preselect=preselect
    )
    if selected < 0:
        return ""
    if selected == len(labels) - 1:
        folder = create_managed_folder(root)
    else:
        folder = paths[selected]
    if folder:
        remember_folder(system, folder)
    return folder


def library_manifest(root):
    files = []
    stack = [(root, "")]
    while stack:
        current, relative_root = stack.pop()
        directories, filenames = xbmcvfs.listdir(current)
        for filename in filenames:
            relative = "/".join(
                value for value in (relative_root, filename) if value
            )
            source = join_vfs(current, filename)
            try:
                size = int(xbmcvfs.Stat(source).st_size())
            except Exception:
                size = 0
            files.append((source, relative, size))
        for dirname in directories:
            name = dirname.rstrip("/\\")
            if not name or name in (".", ".."):
                continue
            relative = "/".join(
                value for value in (relative_root, name) if value
            )
            stack.append((join_vfs(current, name), relative))
    return files


def remove_empty_library_tree(root):
    try:
        directories, _ = xbmcvfs.listdir(root)
    except Exception:
        return
    for dirname in directories:
        child = join_vfs(root, dirname.rstrip("/\\"))
        remove_empty_library_tree(child)
        try:
            xbmcvfs.rmdir(child)
        except Exception:
            pass


def migrate_saved_paths(old_root, new_root):
    history = load_history()
    for entry in history:
        for key in ("destination", "archive_path", "extracted_path"):
            entry[key] = storage.rebase_path(entry.get(key, ""), old_root, new_root)
        entry["extracted_files"] = [
            storage.rebase_path(path, old_root, new_root)
            for path in (entry.get("extracted_files") or [])
        ]
    save_history(history)
    state = folder_state()
    for key, value in list(state.items()):
        if isinstance(value, str):
            state[key] = storage.rebase_path(value, old_root, new_root)
    save_json_file(FOLDER_STATE_PATH, state)


def migrate_rom_library():
    old_root = download_folder().rstrip("/\\")
    dialog = xbmcgui.Dialog()
    try:
        parent = dialog.browseSingle(
            0, "Choose a parent folder for DB3 ROMs", "files", "", False, False, ""
        )
    except (AttributeError, TypeError):
        parent = dialog.browse(
            0, "Choose a parent folder for DB3 ROMs", "files", "", False, False, ""
        )
    if not parent:
        return
    parent = xbmcvfs.translatePath(parent).rstrip("/\\")
    new_root = join_vfs(parent, "DB3 ROMs")
    old_local = os.path.realpath(xbmcvfs.translatePath(old_root))
    new_local = os.path.realpath(xbmcvfs.translatePath(new_root))
    if old_local == new_local:
        notify("The ROM Library is already in that location.")
        return
    if storage.is_within(old_local, new_local) or storage.is_within(
        new_local, old_local
    ):
        notify("Choose a folder outside the current ROM Library.", error=True)
        return
    if path_exists(new_root):
        directories, files = xbmcvfs.listdir(new_root)
        if directories or files:
            notify("The destination DB3 ROMs folder must be empty.", error=True)
            return
    manifest = library_manifest(old_root)
    total_size = sum(item[2] for item in manifest)
    if not enough_space(parent, total_size):
        notify("There is not enough free storage for the ROM Library.", error=True)
        return
    if not dialog.yesno(
        ADDON_NAME,
        "Move {} file(s) to {}?".format(len(manifest), new_root),
        "Originals are removed only after every copy is verified.",
    ):
        return
    if not xbmcvfs.mkdirs(new_root) and not path_exists(new_root):
        notify("Kodi could not create the destination folder.", error=True)
        return
    progress = xbmcgui.DialogProgress()
    progress.create(ADDON_NAME, "Moving ROM Library")
    copied = []
    completed = 0
    try:
        for source, relative, size in manifest:
            if progress.iscanceled():
                raise InterruptedError("Migration cancelled.")
            destination = join_vfs(new_root, relative)
            xbmcvfs.mkdirs(os.path.dirname(destination))
            if not xbmcvfs.copy(source, destination):
                xbmcvfs.delete(destination)
                raise RuntimeError("Kodi could not copy {}.".format(relative))
            copied.append((source, destination))
            try:
                copied_size = int(xbmcvfs.Stat(destination).st_size())
            except Exception:
                copied_size = -1
            if copied_size != size:
                raise RuntimeError("Verification failed for {}.".format(relative))
            completed += size
            percent = int(completed * 100 / total_size) if total_size else 100
            progress.update(min(percent, 100), relative)
    except (InterruptedError, RuntimeError) as exc:
        for _, destination in reversed(copied):
            xbmcvfs.delete(destination)
        remove_empty_library_tree(new_root)
        xbmcvfs.rmdir(new_root)
        notify(str(exc), error=not isinstance(exc, InterruptedError))
        return
    finally:
        progress.close()
    undeleted = 0
    for source, _ in copied:
        if path_exists(source) and not xbmcvfs.delete(source):
            undeleted += 1
    ADDON.setSetting("download_folder", new_root.rstrip("/\\") + "/")
    migrate_saved_paths(old_root, new_root)
    ensure_system_folders(force=True)
    remove_empty_library_tree(old_root)
    if undeleted:
        notify(
            "Library moved, but {} old file(s) could not be removed.".format(undeleted),
            error=True,
        )
    else:
        notify("ROM Library moved to {}.".format(new_root))


def folder_manager():
    choice = xbmcgui.Dialog().select(
        "ROM folder manager",
        (
            "Create folder",
            "Rename folder",
            "Delete empty folder",
            "Create/repair standard system folders",
            "Move entire ROM Library",
        ),
    )
    if choice < 0:
        return
    if choice == 4:
        migrate_rom_library()
        return
    root, names = managed_folders()
    if choice == 0:
        create_managed_folder(root)
        return
    if choice == 3:
        ensure_system_folders(force=True)
        notify("Standard system folders are ready.")
        return
    selected = xbmcgui.Dialog().select(
        "Choose folder", names
    )
    if selected < 0:
        return
    old_path = join_vfs(root, names[selected])
    if choice == 1:
        new_name = xbmcgui.Dialog().input(
            "Rename ROM folder", defaultt=names[selected],
            type=xbmcgui.INPUT_ALPHANUM,
        ).strip()
        new_name = storage.safe_component(new_name, "")
        if not new_name:
            return
        new_path = join_vfs(root, new_name)
        if path_exists(new_path):
            notify("That folder already exists.", error=True)
        elif xbmcvfs.rename(old_path, new_path):
            state = folder_state()
            for key, value in list(state.items()):
                if value == old_path:
                    state[key] = new_path
            save_json_file(FOLDER_STATE_PATH, state)
            notify("Folder renamed.")
        else:
            notify("Kodi could not rename that folder.", error=True)
        return
    directories, files = xbmcvfs.listdir(old_path)
    if directories or files:
        notify("Only empty folders can be deleted.", error=True)
        return
    if xbmcgui.Dialog().yesno(ADDON_NAME, "Delete empty folder {}?".format(names[selected])):
        if xbmcvfs.rmdir(old_path):
            notify("Folder deleted.")
        else:
            notify("Kodi could not delete that folder.", error=True)


def enough_space(folder, required):
    if not required:
        return True
    try:
        local = xbmcvfs.translatePath(folder)
        while local and not os.path.exists(local):
            parent = os.path.dirname(local)
            if parent == local:
                break
            local = parent
        free = shutil.disk_usage(local).free
        return free >= int(required) + (32 * 1024 * 1024)
    except (OSError, ValueError):
        return True


def safe_delete_file(path):
    root = xbmcvfs.translatePath(download_folder())
    local = xbmcvfs.translatePath(path)
    if not storage.is_within(root, local):
        raise RuntimeError("Refusing to delete a file outside the ROM Library.")
    if path_exists(path) and not xbmcvfs.delete(path):
        raise RuntimeError("Kodi could not delete {}.".format(os.path.basename(path)))


def move_managed_file(source, destination):
    root = xbmcvfs.translatePath(download_folder())
    local_source = xbmcvfs.translatePath(source)
    local_destination = xbmcvfs.translatePath(destination)
    if not storage.is_within(root, local_source) or not storage.is_within(
        root, local_destination
    ):
        raise RuntimeError("Files can only be moved inside the ROM Library.")
    if os.path.realpath(local_source) == os.path.realpath(local_destination):
        raise RuntimeError("That file is already in the selected folder.")
    if not path_exists(source):
        raise RuntimeError("The downloaded file no longer exists.")
    parent = os.path.dirname(destination)
    if parent:
        xbmcvfs.mkdirs(parent)
    if xbmcvfs.rename(source, destination):
        return
    if not xbmcvfs.copy(source, destination):
        raise RuntimeError("Kodi could not copy the file to that folder.")
    if not xbmcvfs.delete(source):
        xbmcvfs.delete(destination)
        raise RuntimeError("Kodi copied the file but could not remove the original.")


def safe_delete_folder(path):
    root = xbmcvfs.translatePath(download_folder())
    local = xbmcvfs.translatePath(path)
    if not storage.is_within(root, local):
        raise RuntimeError("Refusing to delete a folder outside the ROM Library.")
    if not path_exists(path):
        return
    try:
        shutil.rmtree(local)
    except OSError as exc:
        raise RuntimeError("Could not delete extracted folder: {}".format(exc))


def cleanup_empty_parents(path, stop):
    current = os.path.dirname(xbmcvfs.translatePath(path))
    stop = os.path.realpath(xbmcvfs.translatePath(stop))
    while current and storage.is_within(stop, current):
        try:
            os.rmdir(current)
        except OSError:
            break
        current = os.path.dirname(current)


def zip_manifest(archive_path, extraction_folder):
    manifest = []
    total = 0
    with zipfile.ZipFile(xbmcvfs.translatePath(archive_path)) as archive:
        for info in archive.infolist():
            relative = storage.safe_archive_member(info.filename)
            if not relative:
                if info.filename.rstrip("/\\"):
                    raise RuntimeError("The archive contains an unsafe file path.")
                continue
            unix_type = (info.external_attr >> 16) & 0o170000
            if unix_type == stat.S_IFLNK:
                raise RuntimeError("Symbolic links are not extracted for safety.")
            destination = join_vfs(extraction_folder, relative)
            manifest.append((info, destination))
            if not info.is_dir():
                total += int(info.file_size or 0)
    return manifest, total


def extract_zip(archive_path, extraction_folder):
    manifest, required = zip_manifest(archive_path, extraction_folder)
    if not enough_space(extraction_folder, required):
        raise RuntimeError("There is not enough free storage for this archive.")
    conflicts = [destination for info, destination in manifest
                 if not info.is_dir() and path_exists(destination)]
    if conflicts and not xbmcgui.Dialog().yesno(
        ADDON_NAME,
        "{} extracted file(s) already exist.".format(len(conflicts)),
        "Overwrite them?",
    ):
        raise InterruptedError("Extraction cancelled.")
    progress = xbmcgui.DialogProgress()
    progress.create(ADDON_NAME, "Extracting {}".format(os.path.basename(archive_path)))
    extracted = []
    completed = 0
    try:
        xbmcvfs.mkdirs(extraction_folder)
        with zipfile.ZipFile(xbmcvfs.translatePath(archive_path)) as archive:
            for info, destination in manifest:
                if progress.iscanceled():
                    raise InterruptedError("Extraction cancelled.")
                if info.is_dir():
                    xbmcvfs.mkdirs(destination)
                    continue
                if info.flag_bits & 0x1:
                    raise RuntimeError("Encrypted ZIP files are not supported.")
                parent = os.path.dirname(destination)
                if parent:
                    xbmcvfs.mkdirs(parent)
                with archive.open(info) as source, open(
                    xbmcvfs.translatePath(destination), "wb"
                ) as output:
                    shutil.copyfileobj(source, output, 256 * 1024)
                extracted.append(destination)
                completed += int(info.file_size or 0)
                percent = int(completed * 100 / required) if required else 0
                progress.update(min(percent, 100), info.filename)
    except Exception:
        for path in reversed(extracted):
            try:
                xbmcvfs.delete(path)
            except Exception:
                pass
        raise
    finally:
        progress.close()
    return extracted


def archive_vfs_roots(archive_path, kind):
    encoded = quote(xbmcvfs.translatePath(archive_path).replace("\\", "/"), safe="")
    if kind == "rar":
        return ("rar://{}/".format(encoded), "archive://{}/".format(encoded))
    return ("archive://{}/".format(encoded),)


def vfs_child(root, relative, directory=False):
    parts = [quote(part, safe="") for part in relative.split("/") if part]
    value = root + "/".join(parts)
    return value.rstrip("/") + "/" if directory else value


def vfs_archive_manifest(root, relative=""):
    current = vfs_child(root, relative, directory=True) if relative else root
    directories, files = xbmcvfs.listdir(current)
    manifest = []
    for filename in files:
        safe_name = storage.safe_archive_member(filename)
        if not safe_name:
            raise RuntimeError("The archive contains an unsafe file name.")
        child = "/".join(value for value in (relative, safe_name) if value)
        source = vfs_child(root, child)
        try:
            size = int(xbmcvfs.Stat(source).st_size())
        except Exception:
            size = 0
        manifest.append((source, child, size))
    for dirname in directories:
        safe_name = storage.safe_archive_member(dirname.rstrip("/\\"))
        if not safe_name:
            raise RuntimeError("The archive contains an unsafe folder name.")
        child = "/".join(value for value in (relative, safe_name) if value)
        manifest.extend(vfs_archive_manifest(root, child))
    return manifest


def extract_vfs_archive(archive_path, extraction_folder, kind):
    manifest = []
    for root in archive_vfs_roots(archive_path, kind):
        try:
            manifest = vfs_archive_manifest(root)
        except Exception:
            manifest = []
        if manifest:
            break
    if not manifest:
        if not xbmc.getCondVisibility("System.HasAddon(vfs.libarchive)"):
            if xbmcgui.Dialog().yesno(
                ADDON_NAME,
                "RAR and 7z extraction needs Kodi's Archive support.",
                "Install it now?",
            ):
                xbmc.executebuiltin("InstallAddon(vfs.libarchive)", wait=True)
        raise RuntimeError(
            "Kodi could not open this {} archive. Install or enable VFS Archive Support."
            .format(kind.upper())
        )
    required = sum(item[2] for item in manifest)
    if not enough_space(extraction_folder, required):
        raise RuntimeError("There is not enough free storage for this archive.")
    conflicts = [join_vfs(extraction_folder, relative)
                 for _, relative, _ in manifest
                 if path_exists(join_vfs(extraction_folder, relative))]
    if conflicts and not xbmcgui.Dialog().yesno(
        ADDON_NAME,
        "{} extracted file(s) already exist.".format(len(conflicts)),
        "Overwrite them?",
    ):
        raise InterruptedError("Extraction cancelled.")
    progress = xbmcgui.DialogProgress()
    progress.create(ADDON_NAME, "Extracting {}".format(os.path.basename(archive_path)))
    extracted = []
    copied = 0
    try:
        xbmcvfs.mkdirs(extraction_folder)
        for source, relative, size in manifest:
            if progress.iscanceled():
                raise InterruptedError("Extraction cancelled.")
            destination = join_vfs(extraction_folder, relative)
            xbmcvfs.mkdirs(os.path.dirname(destination))
            if path_exists(destination):
                xbmcvfs.delete(destination)
            if not xbmcvfs.copy(source, destination):
                raise RuntimeError("Kodi could not extract {}.".format(relative))
            extracted.append(destination)
            copied += size
            percent = int(copied * 100 / required) if required else 0
            progress.update(min(percent, 100), relative)
    except Exception:
        for path in reversed(extracted):
            try:
                xbmcvfs.delete(path)
            except Exception:
                pass
        raise
    finally:
        progress.close()
    return extracted


def extract_archive(archive_path, extraction_folder, kind):
    if kind == "zip":
        return extract_zip(archive_path, extraction_folder)
    if kind in ("rar", "7z"):
        return extract_vfs_archive(archive_path, extraction_folder, kind)
    raise RuntimeError("This file type cannot be extracted.")


def load_history():
    if not xbmcvfs.exists(HISTORY_PATH):
        return []
    handle = None
    try:
        handle = xbmcvfs.File(HISTORY_PATH)
        content = handle.read()
        return json.loads(content) if content else []
    except Exception as exc:
        log("Could not load history: {}".format(exc), xbmc.LOGWARNING)
        return []
    finally:
        if handle:
            handle.close()


def save_history(entries):
    xbmcvfs.mkdirs(DATA_DIR)
    handle = None
    try:
        handle = xbmcvfs.File(HISTORY_PATH, "w")
        handle.write(json.dumps(entries[:100], ensure_ascii=False, indent=2))
    except Exception as exc:
        log("Could not save history: {}".format(exc), xbmc.LOGWARNING)
    finally:
        if handle:
            handle.close()


def record_download(
    identifier, title, filename, destination, size, system="Other",
    extracted_path="", extracted_files=None, extraction_shared=False,
):
    history = load_history()
    history.insert(
        0,
        {
            "id": str(time.time_ns()),
            "identifier": identifier,
            "title": title,
            "filename": filename,
            "destination": destination,
            "archive_path": destination,
            "system": system,
            "extracted_path": extracted_path,
            "extracted_files": list(extracted_files or []),
            "extraction_shared": bool(extraction_shared),
            "size": int(size or 0),
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    save_history(history)


def download(identifier, filename, title, expected_size=0):
    remote_filename = filename
    filename = safe_filename(remote_filename)
    archive_kind = storage.archive_type(filename)
    extract_requested = False
    if archive_kind:
        selected = xbmcgui.Dialog().select(
            filename,
            ("Download only", "Download and extract"),
        )
        if selected < 0:
            return
        extract_requested = selected == 1
    system = storage.infer_system(title, filename, identifier)
    folder = select_destination(system)
    if not folder:
        return
    destination = join_vfs(folder, filename)
    partial = destination + ".part"
    extraction_folder = ""
    extraction_shared = False
    if extract_requested:
        suggested = storage.archive_folder_name(filename)
        extraction_choice = xbmcgui.Dialog().select(
            "Choose extraction layout",
            (
                "Extract into new folder: {}".format(suggested),
                "Extract directly into {}".format(os.path.basename(folder) or "destination"),
            ),
        )
        if extraction_choice < 0:
            return
        extraction_shared = extraction_choice == 1
        extraction_folder = folder if extraction_shared else join_vfs(folder, suggested)
    if not enough_space(folder, expected_size):
        notify("There is not enough free storage for this download.", error=True)
        return
    if xbmcvfs.exists(destination):
        overwrite = xbmcgui.Dialog().yesno(
            ADDON_NAME,
            "{} already exists.".format(filename),
            "Download it again?",
        )
        if not overwrite:
            return

    url = ia_client.download_url(identifier, remote_filename)
    request = Request(url, headers={"User-Agent": USER_AGENT})
    opener, _ = session_opener()
    progress = xbmcgui.DialogProgress()
    progress.create(ADDON_NAME, "Starting {}".format(filename))
    output = None
    downloaded = 0
    try:
        with opener.open(request, timeout=30) as response:
            total = int(response.headers.get("Content-Length") or expected_size or 0)
            output = xbmcvfs.File(partial, "w")
            while True:
                if progress.iscanceled():
                    raise InterruptedError("Download cancelled.")
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                percent = int(downloaded * 100 / total) if total else 0
                progress.update(
                    min(percent, 100),
                    "{} of {}".format(
                        ia_client.format_size(downloaded),
                        ia_client.format_size(total) if total else "unknown size",
                    ),
                )
        if output:
            output.close()
            output = None
        if xbmcvfs.exists(destination):
            xbmcvfs.delete(destination)
        if not xbmcvfs.rename(partial, destination):
            raise RuntimeError("Kodi could not move the completed file.")
        progress.close()
        extracted_files = []
        if extract_requested:
            try:
                extracted_files = extract_archive(
                    destination, extraction_folder, archive_kind
                )
            except InterruptedError:
                notify("Extraction cancelled. The archive was kept.")
            except (
                OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile
            ) as exc:
                log("Extraction failed: {}".format(exc), xbmc.LOGERROR)
                notify("Extraction failed: {}".format(exc), error=True)
        extracted_path = extraction_folder if extracted_files else ""
        record_download(
            identifier,
            title,
            filename,
            destination,
            downloaded,
            system=system,
            extracted_path=extracted_path,
            extracted_files=extracted_files,
            extraction_shared=extraction_shared,
        )
        if extracted_files and get_bool_setting("delete_archive_after_extract"):
            try:
                safe_delete_file(destination)
            except RuntimeError as exc:
                notify(str(exc), error=True)
        if extracted_files:
            notify("Downloaded and extracted {}".format(filename))
        else:
            notify("Downloaded {}".format(filename))
    except InterruptedError:
        notify("Download cancelled.")
        if xbmcvfs.exists(partial):
            xbmcvfs.delete(partial)
    except (HTTPError, URLError, OSError, RuntimeError) as exc:
        log("Download failed: {}".format(exc), xbmc.LOGERROR)
        notify("Download failed: {}".format(exc), error=True)
        if xbmcvfs.exists(partial):
            xbmcvfs.delete(partial)
    finally:
        if output:
            output.close()
        progress.close()


def scan_game_files(folder, stop_after_one=False):
    root = xbmcvfs.translatePath(download_folder())
    local_folder = xbmcvfs.translatePath(folder)
    if not storage.is_within(root, local_folder):
        return []
    results = []
    stack = [(folder, "")]
    visited = set()
    while stack:
        current, relative_root = stack.pop()
        local_current = os.path.realpath(xbmcvfs.translatePath(current))
        if local_current in visited or not storage.is_within(root, local_current):
            continue
        visited.add(local_current)
        try:
            directories, files = xbmcvfs.listdir(current)
        except Exception as exc:
            log("Could not scan {}: {}".format(current, exc), xbmc.LOGWARNING)
            continue
        for filename in files:
            if not storage.is_game_file(filename):
                continue
            path = join_vfs(current, filename)
            relative = "/".join(
                value for value in (relative_root, filename) if value
            )
            try:
                size = int(xbmcvfs.Stat(path).st_size())
            except Exception:
                size = 0
            results.append({"path": path, "relative": relative, "size": size})
            if stop_after_one:
                return results
        for dirname in reversed(directories):
            name = dirname.rstrip("/\\")
            if not name or name in (".", ".."):
                continue
            relative = "/".join(
                value for value in (relative_root, name) if value
            )
            stack.append((join_vfs(current, name), relative))
    return sorted(results, key=lambda item: item["relative"].lower())


def show_game_library():
    xbmcplugin.setPluginCategory(HANDLE, "My Games")
    xbmcplugin.setContent(HANDLE, "files")
    root, names = managed_folders()
    found = False
    for name in names:
        folder = join_vfs(root, name)
        if not scan_game_files(folder, stop_after_one=True):
            continue
        found = True
        add_folder(
            name,
            "library_system",
            label2="Games available",
            folder=folder,
            system=name,
        )
    if not found:
        notify("No downloaded games were found in your system folders.")
    finish(view_mode=DB3_LIST_VIEW)


def show_game_system(folder, system):
    xbmcplugin.setPluginCategory(HANDLE, system or "My Games")
    xbmcplugin.setContent(HANDLE, "files")
    files = scan_game_files(folder)
    for entry in files:
        kind = storage.archive_type(entry["relative"])
        details = [ia_client.format_size(entry["size"])]
        if kind:
            details.append("{} archive".format(kind.upper()))
        add_action(
            entry["relative"],
            "library_item",
            label2="  •  ".join(details),
            path=entry["path"],
            system=system,
        )
    if not files:
        notify("No game files were found in {}.".format(system or "this folder"))
    finish(view_mode=DB3_LIST_VIEW)


def show_game_item(path, system):
    root = xbmcvfs.translatePath(download_folder())
    if not storage.is_within(root, xbmcvfs.translatePath(path)):
        notify("That game is outside the managed ROM Library.", error=True)
        return
    kind = storage.archive_type(path)
    choices = ["Play game", "File information"]
    actions = ["play", "info"]
    if kind:
        choices.append("Extract archive")
        actions.append("extract")
    choices.append("Delete game file")
    actions.append("delete")
    selected = xbmcgui.Dialog().select(os.path.basename(path), choices)
    if selected < 0:
        return
    action = actions[selected]
    if action == "play":
        if not path_exists(path):
            notify("That game file no longer exists.", error=True)
            return
        try:
            xbmc.Player().play(path)
        except RuntimeError as exc:
            log("Could not start game: {}".format(exc), xbmc.LOGERROR)
            notify("Kodi could not start this game.", error=True)
        return
    if action == "info":
        try:
            size = int(xbmcvfs.Stat(path).st_size())
        except Exception:
            size = 0
        xbmcgui.Dialog().ok(
            os.path.basename(path),
            "System: {}\nSize: {}\nPath: {}".format(
                system or "Other", ia_client.format_size(size), path
            ),
        )
        return
    if action == "extract":
        target = join_vfs(
            os.path.dirname(path), storage.archive_folder_name(path)
        )
        try:
            extracted = extract_archive(path, target, kind)
            notify("Extracted {} file(s).".format(len(extracted)))
            xbmc.executebuiltin("Container.Refresh")
        except InterruptedError:
            notify("Extraction cancelled.")
        except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            notify("Extraction failed: {}".format(exc), error=True)
        return
    if xbmcgui.Dialog().yesno(
        ADDON_NAME,
        "Delete {}?".format(os.path.basename(path)),
        "This cannot be undone.",
    ):
        try:
            safe_delete_file(path)
            notify("Game file deleted.")
            xbmc.executebuiltin("Container.Refresh")
        except RuntimeError as exc:
            notify(str(exc), error=True)


def show_history():
    xbmcplugin.setPluginCategory(HANDLE, "Download history")
    xbmcplugin.setContent(HANDLE, "files")
    history = load_history()
    add_action("[B]Manage ROM folders[/B]", "folder_manager")
    add_action("Clear missing history entries", "history_clear_missing")
    add_action("Clear all history", "history_clear_all")
    for index, entry in enumerate(history):
        stamp = entry.get("downloaded_at", "").replace("T", " ")[:16]
        state = "Extracted" if entry.get("extracted_path") else "Downloaded"
        label2 = "  •  ".join(
            value
            for value in (
                ia_client.format_size(entry.get("size", 0)),
                entry.get("system", ""),
                state,
                stamp,
            )
            if value
        )
        add_action(
            entry.get("filename") or "Downloaded file",
            "history_item",
            label2=label2,
            index=str(index),
        )
    if not history:
        notify("No downloads have been completed yet.")
    finish(view_mode=DB3_LIST_VIEW)


def entry_archive_path(entry):
    return entry.get("archive_path") or entry.get("destination") or ""


def entry_has_files(entry):
    archive = entry_archive_path(entry)
    if path_exists(archive):
        return True
    extracted_files = entry.get("extracted_files") or []
    if any(path_exists(path) for path in extracted_files):
        return True
    if entry.get("extraction_shared"):
        return False
    extracted = entry.get("extracted_path") or ""
    return path_exists(extracted)


def delete_extracted_entry(entry):
    extracted = entry.get("extracted_path") or ""
    files = entry.get("extracted_files") or []
    if entry.get("extraction_shared"):
        for path in files:
            safe_delete_file(path)
            cleanup_empty_parents(path, extracted)
    elif extracted:
        safe_delete_folder(extracted)
    entry["extracted_path"] = ""
    entry["extracted_files"] = []
    entry["extraction_shared"] = False


def show_history_item(index):
    history = load_history()
    try:
        entry = history[int(index)]
    except (IndexError, TypeError, ValueError):
        notify("That history entry no longer exists.", error=True)
        return
    archive = entry_archive_path(entry)
    extracted = entry.get("extracted_path") or ""
    choices = ["Show file information"]
    actions = ["info"]
    if archive and path_exists(archive):
        choices.append("Move downloaded file")
        actions.append("move")
        choices.append("Delete downloaded archive")
        actions.append("archive")
    if extracted or entry.get("extracted_files"):
        choices.append("Delete extracted files")
        actions.append("extracted")
    if path_exists(archive) and (extracted or entry.get("extracted_files")):
        choices.append("Delete archive and extracted files")
        actions.append("both")
    choices.append("Remove from history only")
    actions.append("history")
    selected = xbmcgui.Dialog().select(entry.get("filename") or "Downloaded file", choices)
    if selected < 0:
        return
    action = actions[selected]
    if action == "info":
        details = [
            "Archive: {}".format(archive or "Not recorded"),
            "Extracted: {}".format(extracted or "No"),
            "System: {}".format(entry.get("system") or "Other"),
        ]
        xbmcgui.Dialog().ok("Downloaded file", "\n".join(details))
        return
    if action == "move":
        folder = select_destination(entry.get("system") or "Other")
        if not folder:
            return
        destination = join_vfs(folder, os.path.basename(archive))
        if path_exists(destination):
            if os.path.realpath(xbmcvfs.translatePath(archive)) == os.path.realpath(
                xbmcvfs.translatePath(destination)
            ):
                notify("That file is already in the selected folder.")
                return
            if not xbmcgui.Dialog().yesno(
                ADDON_NAME,
                "A file with that name already exists.",
                "Replace it?",
            ):
                return
            try:
                safe_delete_file(destination)
            except RuntimeError as exc:
                notify(str(exc), error=True)
                return
        try:
            move_managed_file(archive, destination)
            entry["archive_path"] = destination
            entry["destination"] = destination
            if os.path.realpath(xbmcvfs.translatePath(folder)) != os.path.realpath(
                xbmcvfs.translatePath(download_folder())
            ):
                entry["system"] = os.path.basename(folder.rstrip("/\\"))
            save_history(history)
            notify("File moved to {}.".format(os.path.basename(folder.rstrip("/\\"))))
            xbmc.executebuiltin("Container.Refresh")
        except RuntimeError as exc:
            notify(str(exc), error=True)
        return
    if not xbmcgui.Dialog().yesno(
        ADDON_NAME,
        "Confirm {}?".format(choices[selected].lower()),
    ):
        return
    try:
        if action in ("archive", "both") and archive:
            safe_delete_file(archive)
            entry["archive_path"] = ""
            entry["destination"] = ""
        if action in ("extracted", "both"):
            delete_extracted_entry(entry)
        if action in ("history", "both") or not entry_has_files(entry):
            history.pop(int(index))
        save_history(history)
        notify("Download history updated.")
        xbmc.executebuiltin("Container.Refresh")
    except RuntimeError as exc:
        notify(str(exc), error=True)


def clear_missing_history():
    history = load_history()
    remaining = [entry for entry in history if entry_has_files(entry)]
    removed = len(history) - len(remaining)
    save_history(remaining)
    notify("Removed {} missing entr{}.".format(removed, "y" if removed == 1 else "ies"))
    xbmc.executebuiltin("Container.Refresh")


def clear_all_history():
    if xbmcgui.Dialog().yesno(
        ADDON_NAME,
        "Clear all download history?",
        "Downloaded and extracted files will be kept.",
    ):
        save_history([])
        notify("Download history cleared.")
        xbmc.executebuiltin("Container.Refresh")


def route():
    action = PARAMS.get("action", "root")
    if action == "root":
        show_root()
    elif action == "homebrew":
        show_results(ia_client.homebrew_query(), 1, "Homebrew")
    elif action == "public_domain":
        show_results(ia_client.public_domain_query(), 1, "Public domain")
    elif action == "search":
        search()
    elif action == "search_results":
        term = PARAMS.get("term", "").strip()
        show_results(
            ia_client.user_search_query(term),
            int(PARAMS.get("page", "1")),
            'Search: "{}"'.format(term),
        )
    elif action == "browse":
        show_results(
            PARAMS.get("query", ""),
            int(PARAMS.get("page", "1")),
            PARAMS.get("heading", ADDON_NAME),
        )
    elif action == "files":
        show_files(
            PARAMS.get("identifier", ""),
            PARAMS.get("title", ""),
        )
    elif action == "download":
        download(
            PARAMS.get("identifier", ""),
            PARAMS.get("filename", ""),
            PARAMS.get("title", ""),
            int(PARAMS.get("size", "0") or 0),
        )
        finish()
    elif action == "history":
        show_history()
    elif action == "library":
        show_game_library()
    elif action == "library_system":
        show_game_system(
            PARAMS.get("folder", ""),
            PARAMS.get("system", ""),
        )
    elif action == "library_item":
        show_game_item(
            PARAMS.get("path", ""),
            PARAMS.get("system", ""),
        )
        finish()
    elif action == "history_item":
        show_history_item(PARAMS.get("index", ""))
        finish()
    elif action == "history_clear_missing":
        clear_missing_history()
        finish()
    elif action == "history_clear_all":
        clear_all_history()
        finish()
    elif action == "folder_manager":
        folder_manager()
        finish()
    elif action == "migrate_library":
        migrate_rom_library()
        finish()
    elif action == "settings":
        ADDON.openSettings()
        finish()
    elif action == "login":
        sign_in()
    elif action == "logout":
        sign_out()
    else:
        notify("Unknown add-on action.", error=True)
        finish(False)


if __name__ == "__main__":
    try:
        route()
    except Exception as exc:
        log("Unexpected error: {}".format(exc), xbmc.LOGERROR)
        notify("Unexpected error: {}".format(exc), error=True)
        finish(False)

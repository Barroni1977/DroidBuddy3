"""Kodi entry point for DB3 ROM Browser."""

import json
import os
import re
import sys
import time
from http.cookiejar import MozillaCookieJar
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from resources.lib import ia_client


ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_NAME = ADDON.getAddonInfo("name")
ADDON_ICON = ADDON.getAddonInfo("icon")
HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]
PARAMS = dict(parse_qsl(sys.argv[2][1:] if len(sys.argv) > 2 else ""))
USER_AGENT = "DB3-ROM-Browser/0.2.4 Kodi"
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
        return
    show_results(ia_client.user_search_query(term), 1, 'Search: "{}"'.format(term))


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


def show_files(identifier, title, show_all=False):
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

    show_all = show_all or get_bool_setting("show_all_files")
    files = ia_client.filter_rom_files(
        metadata.get("files") or [],
        extensions=allowed_extensions(),
        show_all=show_all,
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

    if not show_all:
        add_folder(
            "[B]Show all files in this item[/B]",
            "files",
            identifier=identifier,
            title=title,
            all="1",
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


def record_download(identifier, title, filename, destination, size):
    history = load_history()
    history.insert(
        0,
        {
            "identifier": identifier,
            "title": title,
            "filename": filename,
            "destination": destination,
            "size": int(size or 0),
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    save_history(history)


def download(identifier, filename, title, expected_size=0):
    remote_filename = filename
    filename = safe_filename(remote_filename)
    folder = download_folder()
    destination = join_vfs(folder, filename)
    partial = destination + ".part"
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
        record_download(identifier, title, filename, destination, downloaded)
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


def show_history():
    xbmcplugin.setPluginCategory(HANDLE, "Download history")
    xbmcplugin.setContent(HANDLE, "files")
    history = load_history()
    for entry in history:
        stamp = entry.get("downloaded_at", "").replace("T", " ")[:16]
        label2 = "  •  ".join(
            value
            for value in (
                ia_client.format_size(entry.get("size", 0)),
                stamp,
            )
            if value
        )
        add_action(
            entry.get("filename") or "Downloaded file",
            "history_item",
            label2=label2,
            destination=entry.get("destination", ""),
            identifier=entry.get("identifier", ""),
        )
    if not history:
        notify("No downloads have been completed yet.")
    finish(view_mode=DB3_LIST_VIEW)


def show_history_item(destination, identifier):
    xbmcgui.Dialog().ok(
        "Downloaded file",
        destination or "The saved path is unavailable.",
        ia_client.details_url(identifier) if identifier else "",
    )


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
            PARAMS.get("all") == "1",
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
    elif action == "history_item":
        show_history_item(
            PARAMS.get("destination", ""), PARAMS.get("identifier", "")
        )
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

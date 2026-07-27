import os
import sys
import re
import json
import time
import random
import asyncio
import threading
import subprocess
import urllib.parse

from colorama import init as colorama_init, Fore, Style
import requests
import websockets

colorama_init(autoreset=True)

LUA_URL = "https://raw.githubusercontent.com/TeamUBHub/Grow-Farmer/refs/heads/main/ClientLoader.lua"
DATA_FILE = os.path.join(os.path.expanduser("~"), ".roblox_manager_accounts.json")
STATE_FILE = os.path.join(os.path.expanduser("~"), ".roblox_manager_state.json")
WS_HOST = "127.0.0.1"
WS_PORT = 8765
ROBLOX_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def info(msg):    print(f"{Fore.CYAN}[i]{Style.RESET_ALL} {msg}")
def ok(msg):       print(f"{Fore.GREEN}[+]{Style.RESET_ALL} {msg}")
def warn(msg):     print(f"{Fore.YELLOW}[!]{Style.RESET_ALL} {msg}")
def err(msg):       print(f"{Fore.RED}[x]{Style.RESET_ALL} {msg}")
def header(msg):
    print(f"\n{Fore.MAGENTA}{Style.BRIGHT}== {msg} =={Style.RESET_ALL}")


def load_accounts() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_accounts(accounts: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=2)


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def set_last_launched(username: str, server_label: str = ""):
    state = load_state()
    state["last_launched"] = {"username": username, "server": server_label}
    save_state(state)


def all_targets() -> list:
    """Flattened list of (username, server_label) pairs across all accounts and all their servers."""
    accounts = load_accounts()
    targets = []
    for username, entry in accounts.items():
        servers = entry.get("servers", [])
        if not servers:
            continue
        for s in servers:
            targets.append((username, s["label"]))
    return targets


def next_target() -> tuple[str, str] | None:
    targets = all_targets()
    if not targets:
        return None
    state = load_state()
    last = state.get("last_launched") or {}
    last_pair = (last.get("username"), last.get("server"))
    if last_pair not in targets:
        return targets[0]
    idx = targets.index(last_pair)
    return targets[(idx + 1) % len(targets)]


def validate_cookie(cookie: str) -> dict | None:
    try:
        r = requests.get(
            "https://users.roblox.com/v1/users/authenticated",
            cookies={".ROBLOSECURITY": cookie},
            headers={"User-Agent": ROBLOX_USER_AGENT},
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            return {
                "name": data.get("name", "?"),
                "id": str(data.get("id", "")),
                "displayName": data.get("displayName", data.get("name", "?")),
            }
    except Exception:
        pass
    return None


def browser_login() -> tuple[str, str] | None:
    try:
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
        from selenium.webdriver.edge.service import Service
        from webdriver_manager.microsoft import EdgeChromiumDriverManager
    except ImportError as ie:
        missing = str(ie).split("'")[1] if "'" in str(ie) else str(ie)
        err(f"Missing package: {missing}")
        info("Run: pip install selenium webdriver-manager")
        return None

    info("Opening browser — log in to Roblox, then this will continue automatically.")
    opts = Options()
    opts.add_argument("--start-maximized")
    driver = webdriver.Edge(
        service=Service(EdgeChromiumDriverManager().install()), options=opts
    )
    driver.get("https://www.roblox.com/login")

    cookie = None
    tick = 0
    try:
        while True:
            time.sleep(0.5)
            tick += 1
            if tick % 20 == 0:
                info("Still waiting for login...")
            try:
                c = driver.get_cookie(".ROBLOSECURITY")
            except Exception:
                break
            if c and c.get("value") and len(c["value"]) > 100:
                cookie = c["value"]
                break
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    if not cookie:
        warn("Login window closed before completing sign-in.")
        return None

    user_info = validate_cookie(cookie)
    if not user_info:
        err("Got a cookie but couldn't validate it against Roblox.")
        return None

    return user_info["name"], cookie


def parse_private_server_url(url: str) -> tuple[str | None, str | None]:
    place_match = re.search(r"/games/(\d+)", url)
    code_match = re.search(r"privateServerLinkCode=([\w-]+)", url)
    place_id = place_match.group(1) if place_match else None
    link_code = code_match.group(1) if code_match else None
    return place_id, link_code


def is_share_link(url: str) -> bool:
    return "/share" in url and "type=Server" in url


def resolve_share_link(cookie: str, url: str) -> tuple[str | None, str | None]:
    try:
        r = requests.get(
            url, cookies={".ROBLOSECURITY": cookie},
            headers={"User-Agent": "Mozilla/5.0"},
            allow_redirects=True, timeout=10,
        )
    except Exception:
        return None, None

    place_id, link_code = parse_private_server_url(r.url)
    if place_id and link_code:
        return place_id, link_code

    m_place = re.search(r'"placeId":(\d+)', r.text) or re.search(r"/games/(\d+)", r.text)
    m_code = re.search(r"Roblox\.GameLauncher\.joinPrivateGame\(\d+,\s*'([\w-]+)'", r.text) \
        or re.search(r'"privateServerLinkCode":"([\w-]+)"', r.text)
    place_id = place_id or (m_place.group(1) if m_place else None)
    link_code = link_code or (m_code.group(1) if m_code else None)
    return place_id, link_code


def resolve_server_link(cookie: str, url: str) -> tuple[str | None, str | None]:
    """Resolve either the old privateServerLinkCode= format or the new /share?code= format."""
    if is_share_link(url):
        return resolve_share_link(cookie, url)
    return parse_private_server_url(url)


def prompt_one_server(cookie: str) -> dict | None:
    label = input("Label for this server (e.g. 'Main', 'Farm 1', leave blank to stop): ").strip()
    if not label:
        return None
    print("  1. Public place (just a place ID)")
    print("  2. Private server invite link")
    print("  3. Browse private servers this account already has access to")
    mode = input("  Choice: ").strip()

    if mode == "3":
        place_id = input("  Place ID to browse (blank = 97598239454123): ").strip() or "97598239454123"
        if not place_id:
            warn("  No place ID given, skipping.")
            return {"label": label, "place_id": "", "private_server_link": "", "job_id": ""}
        info("  Fetching private servers you have access to...")
        servers = list_my_private_servers(cookie, place_id)
        if not servers:
            warn("  No accessible private servers found for that place (or the request failed).")
            return {"label": label, "place_id": place_id, "private_server_link": "", "job_id": ""}
        info(f"  (debug) fields on first result: {sorted(servers[0].keys())}")
        for i, s in enumerate(servers, 1):
            name = s.get("name") or s.get("id", "?")
            playing = s.get("playing", "?")
            max_players = s.get("maxPlayers", "?")
            print(f"    {i}. {name}  ({playing}/{max_players} players)")
        sel = input("  Which one? ").strip()
        try:
            chosen = servers[int(sel) - 1]
        except (ValueError, IndexError):
            err("  Invalid choice, skipping.")
            return {"label": label, "place_id": place_id, "private_server_link": "", "job_id": ""}
        access_code = chosen.get("accessCode", "")
        if access_code:
            ok("  Got an access code directly — no link resolution needed at launch.")
            return {"label": label, "place_id": place_id, "private_server_link": "",
                     "job_id": "", "access_code": access_code}
        return {"label": label, "place_id": place_id, "private_server_link": "",
                 "job_id": chosen.get("id", ""), "access_code": ""}

    if mode == "2":
        link = input("  Paste the private server invite link: ").strip()
        place_id = ""
        if is_share_link(link):
            info("  Roblox share link detected — will be resolved automatically at launch time.")
        else:
            parsed_place, link_code = parse_private_server_url(link)
            if not parsed_place or not link_code:
                warn("  Couldn't parse a place ID and link code out of that URL — you can fix this later.")
            place_id = parsed_place or ""
        return {"label": label, "place_id": place_id, "private_server_link": link, "job_id": "", "access_code": ""}

    place_id = input("  Public place ID: ").strip()
    return {"label": label, "place_id": place_id, "private_server_link": "", "job_id": "", "access_code": ""}


def prompt_servers_list(cookie: str) -> list:
    info("Add one or more launch targets for this account.")
    servers = []
    while True:
        s = prompt_one_server(cookie)
        if not s:
            break
        servers.append(s)
    return servers


def add_account():
    header("Add account")
    result = browser_login()
    if not result:
        return
    username, cookie = result
    servers = prompt_servers_list(cookie)
    accounts = load_accounts()
    accounts[username] = {"cookie": cookie, "servers": servers}
    save_accounts(accounts)
    ok(f"Saved account: {username}")


def configure_account():
    names = list_accounts()
    if not names:
        return
    sel = input("Number to configure: ").strip()
    try:
        username = names[int(sel) - 1]
    except (ValueError, IndexError):
        err("Invalid choice.")
        return

    accounts = load_accounts()
    entry = accounts[username]
    servers = entry.get("servers", [])

    while True:
        header(f"Configure {username}")
        if servers:
            for i, s in enumerate(servers, 1):
                if s.get("job_id"):
                    target = f"private server instance (place {s.get('place_id','?')})"
                elif s.get("private_server_link"):
                    target = f"private server (place {s.get('place_id','?')})"
                else:
                    target = f"place {s.get('place_id') or 'not set'}"
                print(f"  {i}. {s['label']}  [{target}]")
        else:
            print("  (no servers configured yet)")
        print("  a. Add a server")
        print("  r. Remove a server")
        print("  d. Done")
        choice = input("\n> ").strip().lower()

        if choice == "a":
            s = prompt_one_server(entry["cookie"])
            if s:
                servers.append(s)
        elif choice == "r":
            if not servers:
                warn("Nothing to remove.")
                continue
            rsel = input("Number to remove: ").strip()
            try:
                servers.pop(int(rsel) - 1)
            except (ValueError, IndexError):
                err("Invalid choice.")
        elif choice == "d":
            break
        else:
            warn("Not a valid option.")

    entry["servers"] = servers
    save_accounts(accounts)
    ok(f"Updated servers for {username}")


def remove_account():
    accounts = load_accounts()
    if not accounts:
        warn("No saved accounts.")
        return
    names = list_accounts(accounts)
    choice = input("Number to remove (or blank to cancel): ").strip()
    if not choice:
        return
    try:
        name = names[int(choice) - 1]
    except (ValueError, IndexError):
        err("Invalid choice.")
        return
    del accounts[name]
    save_accounts(accounts)
    ok(f"Removed {name}")


def list_accounts(accounts: dict | None = None) -> list:
    accounts = accounts if accounts is not None else load_accounts()
    if not accounts:
        warn("No saved accounts yet. Choose 'Add account' first.")
        return []
    names = list(accounts.keys())
    header("Saved accounts")
    for i, name in enumerate(names, 1):
        servers = accounts[name].get("servers", [])
        labels = ", ".join(s["label"] for s in servers) if servers else "not configured"
        print(f"  {Fore.WHITE}{i}.{Style.RESET_ALL} {name}  [{labels}]")
    return names


def kill_roblox_processes():
    if os.name != "nt":
        return
    targets = [
        "RobloxPlayerBeta.exe",
        "RobloxPlayerLauncher.exe",
        "RobloxPlayerInstaller.exe",
    ]
    for t in targets:
        subprocess.run(["taskkill", "/F", "/IM", t],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)



def get_csrf_token(cookie: str) -> str | None:
    try:
        r = requests.post(
            "https://auth.roblox.com/v1/authentication-ticket/",
            cookies={".ROBLOSECURITY": cookie},
            headers={
                "Referer": "https://www.roblox.com/",
                "User-Agent": ROBLOX_USER_AGENT,
                "Origin": "https://www.roblox.com",
                "Content-Type": "application/json",
            },
            json={},
            timeout=8,
        )
        token = r.headers.get("x-csrf-token")
        if not token:
            warn(f"CSRF request returned status {r.status_code}, no x-csrf-token header.")
            if r.text:
                warn(f"Response body (first 300 chars): {r.text[:300]}")
        return token
    except Exception as e:
        warn(f"CSRF request failed: {e}")
        return None


def get_auth_ticket(cookie: str, csrf_token: str) -> str | None:
    try:
        r = requests.post(
            "https://auth.roblox.com/v1/authentication-ticket/",
            cookies={".ROBLOSECURITY": cookie},
            headers={
                "X-CSRF-TOKEN": csrf_token,
                "Referer": "https://www.roblox.com/",
                "User-Agent": ROBLOX_USER_AGENT,
                "Origin": "https://www.roblox.com",
                "Content-Type": "application/json",
            },
            json={},
            timeout=8,
        )
        ticket = r.headers.get("rbx-authentication-ticket")
        if not ticket:
            warn(f"Auth ticket request returned status {r.status_code}, no ticket header.")
            if r.text:
                warn(f"Response body (first 300 chars): {r.text[:300]}")
        return ticket
    except Exception as e:
        warn(f"Auth ticket request failed: {e}")
        return None


def resolve_private_server_access_code(cookie: str, place_id: str, link_code: str) -> str | None:
    try:
        url = f"https://www.roblox.com/games/{place_id}/x?privateServerLinkCode={link_code}"
        r = requests.get(
            url,
            cookies={".ROBLOSECURITY": cookie},
            headers={"User-Agent": ROBLOX_USER_AGENT},
            timeout=8,
        )
        m = re.search(r"Roblox\.GameLauncher\.joinPrivateGame\(\d+,\s*'([\w-]+)'", r.text)
        if not m:
            warn(f"Could not find private server access code in page (status {r.status_code}).")
        return m.group(1) if m else None
    except Exception as e:
        warn(f"Private server access code request failed: {e}")
        return None


def list_my_private_servers(cookie: str, place_id: str) -> list:
    """List private server instances for this place that the account already has access to."""
    servers = []
    cursor = ""
    try:
        for _ in range(5):
            url = (f"https://games.roblox.com/v1/games/{place_id}/private-servers"
                   f"?cursor={cursor}&sortOrder=Desc&excludeFullGames=false")
            r = requests.get(
                url,
                cookies={".ROBLOSECURITY": cookie},
                headers={"User-Agent": ROBLOX_USER_AGENT},
                timeout=8,
            )
            if r.status_code != 200:
                warn(f"Listing private servers failed (status {r.status_code}): {r.text[:200]}")
                break
            data = r.json()
            servers.extend(data.get("data", []))
            cursor = data.get("nextPageCursor")
            if not cursor:
                break
    except Exception as e:
        warn(f"Listing private servers failed: {e}")
    return servers


def build_launch_uri_job(ticket: str, place_id: str, job_id: str) -> str:
    browser_tracker_id = f"{random.randint(100000,175000)}{random.randint(100000,900000)}"
    launch_time = int(time.time() * 1000)
    place_launcher_url = (
        f"https://assetgame.roblox.com/game/PlaceLauncher.ashx"
        f"?request=RequestGameJob&placeId={place_id}&gameId={job_id}"
    )
    encoded_launcher_url = urllib.parse.quote(place_launcher_url, safe="")
    return (
        f"roblox-player:1+launchmode:play+gameinfo:{ticket}"
        f"+launchtime:{launch_time}+placelauncherurl:{encoded_launcher_url}"
        f"+browsertrackerid:{browser_tracker_id}+robloxLocale:en_us"
        f"+gameLocale:en_us+channel:+LaunchExp:InApp"
    )


def build_launch_uri(ticket: str, place_id: str, access_code: str | None = None,
                      link_code: str | None = None) -> str:
    browser_tracker_id = f"{random.randint(100000,175000)}{random.randint(100000,900000)}"
    launch_time = int(time.time() * 1000)

    if access_code:
        place_launcher_url = (
            f"https://assetgame.roblox.com/game/PlaceLauncher.ashx"
            f"?request=RequestPrivateGame&placeId={place_id}"
            f"&accessCode={access_code}"
        )
        if link_code:
            place_launcher_url += f"&linkCode={link_code}"
    else:
        place_launcher_url = (
            f"https://assetgame.roblox.com/game/PlaceLauncher.ashx"
            f"?request=RequestGame&placeId={place_id}&isPlayTogetherGame=false"
        )

    encoded_launcher_url = urllib.parse.quote(place_launcher_url, safe="")

    return (
        f"roblox-player:1+launchmode:play+gameinfo:{ticket}"
        f"+launchtime:{launch_time}+placelauncherurl:{encoded_launcher_url}"
        f"+browsertrackerid:{browser_tracker_id}+robloxLocale:en_us"
        f"+gameLocale:en_us+channel:+LaunchExp:InApp"
    )


def resolve_server(entry: dict, server: str | None) -> dict | None:
    servers = entry.get("servers", [])
    if not servers:
        return None
    if not server:
        return servers[0]
    for s in servers:
        if s["label"].lower() == server.lower():
            return s
    try:
        return servers[int(server) - 1]
    except (ValueError, IndexError):
        return None


def launch_account(username: str, server: str | None = None) -> tuple[bool, str]:
    accounts = load_accounts()
    entry = accounts.get(username)
    if not entry:
        err("Unknown account.")
        return False, "Unknown account."

    header(f"Launching {username}")
    info("Checking session...")
    user_info = validate_cookie(entry["cookie"])
    if not user_info:
        err("This saved session has expired. Please re-add the account.")
        return False, "This saved session has expired. Please re-add the account."

    target = resolve_server(entry, server)
    if not target:
        err("No matching server configured for this account. Use 'Configure account' first.")
        return False, "No matching server configured for this account. Use 'Configure account' first."

    place_id = target.get("place_id")
    private_server_link = target.get("private_server_link")
    job_id = target.get("job_id")
    access_code = target.get("access_code") or None
    link_code = None

    if access_code:
        info("Using saved access code — no link resolution needed.")
    elif not job_id and private_server_link:
        if is_share_link(private_server_link):
            info("Resolving share link...")
        parsed_place, link_code = resolve_server_link(entry["cookie"], private_server_link)
        place_id = parsed_place or place_id
        if not link_code or not place_id:
            err("Saved private server link couldn't be resolved. It may be expired, or reconfigure this account.")
            return False, "Saved private server link couldn't be resolved. It may be expired, or reconfigure this account."
        info("Resolving private server access code...")
        access_code = resolve_private_server_access_code(entry["cookie"], place_id, link_code)
        if not access_code:
            err("Could not resolve private server access code. The link may have expired.")
            return False, "Could not resolve private server access code. The link may have expired."

    if not place_id:
        err("No place configured for this server. Use 'Configure account' first.")
        return False, "No place configured for this server. Use 'Configure account' first."

    info("Closing any running Roblox processes...")
    kill_roblox_processes()

    info("Getting CSRF token...")
    csrf = get_csrf_token(entry["cookie"])
    if not csrf:
        err("Failed to get CSRF token. The session may be invalid.")
        return False, "Failed to get CSRF token. The session may be invalid."

    info("Getting authentication ticket...")
    ticket = get_auth_ticket(entry["cookie"], csrf)
    if not ticket:
        err("Failed to get an authentication ticket. The session may be invalid.")
        return False, "Failed to get an authentication ticket. The session may be invalid."

    if job_id:
        uri = build_launch_uri_job(ticket, place_id, job_id)
    else:
        uri = build_launch_uri(ticket, place_id, access_code, link_code)

    info("Launching Roblox...")
    try:
        os.startfile(uri)
    except Exception as e:
        err(f"Failed to launch: {e}")
        return False, f"Failed to launch: {e}"

    set_last_launched(username, target["label"])
    where = "private server instance" if job_id else ("private server" if access_code else f"place {place_id}")
    ok(f"Launched {username} into {where}")
    return True, f"Launched {username} into {where}"


def kill_roblox() -> tuple[bool, str]:
    header("Killing Roblox")
    kill_roblox_processes()
    ok("Roblox processes closed.")
    return True, "Roblox processes closed."


def kill_and_advance() -> tuple[bool, str]:
    header("Kill switch: advancing to next server")
    kill_roblox_processes()

    targets = all_targets()
    if not targets:
        err("Roblox closed, but there are no saved servers to switch to.")
        return False, "Roblox closed, but there are no saved servers to switch to."

    state = load_state()
    last = state.get("last_launched") or {}
    last_pair = (last.get("username"), last.get("server"))
    start_idx = (targets.index(last_pair) + 1) if last_pair in targets else 0

    n = len(targets)
    for offset in range(n):
        idx = (start_idx + offset) % n
        username, server_label = targets[idx]
        success, message = launch_account(username, server_label)
        if success:
            return True, message
        warn(f"Skipping {username} ({server_label}) after failure, trying next...")

    err("Every configured server failed to launch.")
    return False, "Every configured server failed to launch."

def install_client_lua():
    user_profile = os.environ.get("USERPROFILE", "")
    if not user_profile:
        err("USERPROFILE environment variable not found.")
        return

    info(f"Searching for autoexec folders in {user_profile}...")
    autoexec_folders = []

    for root, dirs, _ in os.walk(user_profile):
        for d in dirs:
            if d.lower() == "autoexec":
                autoexec_folders.append(os.path.join(root, d))

    if not autoexec_folders:
        warn("No autoexec folders found.")
        info("Please Download from https://raw.githubusercontent.com/TeamUBHub/Grow-Farmer/refs/heads/main/ClientLoader.lua and add it manually to autoexec.")
        return

    info("Found autoexec directories:")
    for idx, folder in enumerate(autoexec_folders, 1):
        info(f" [{idx}] {folder}")

    selection = 0
    while selection < 1 or selection > len(autoexec_folders):
        try:
            choice = input(f"Select folder number (1-{len(autoexec_folders)}): ")
            selection = int(choice)
        except ValueError:
            pass

    target_folder = autoexec_folders[selection - 1]
    target_file = os.path.join(target_folder, "Client.lua")

    info(f"Downloading Client.lua to: {target_file}...")
    try:
        response = requests.get(LUA_URL, timeout=10)
        response.raise_for_status()
        
        with open(target_file, "wb") as f:
            f.write(response.content)
            
        print(f"Successfully saved Client.lua to {target_file}")
    except Exception as e:
        print(f"Failed to download Client.lua: {e}")


async def ws_handler(websocket):
    remote = websocket.remote_address
    ok(f"Client connected: {remote}")
    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"ok": False, "error": "invalid json"}))
                continue

            action = msg.get("action")
            info(f"[{remote}] action: {action}  {msg}")

            if action == "launch":
                username = msg.get("username", "")
                server = msg.get("server")
                success, message = await asyncio.get_event_loop().run_in_executor(
                    None, launch_account, username, server
                )
                await websocket.send(json.dumps({"ok": success, "message": message}))

            elif action == "kill":
                success, message = await asyncio.get_event_loop().run_in_executor(
                    None, kill_and_advance
                )
                await websocket.send(json.dumps({"ok": success, "message": message}))

            elif action == "stop":
                success, message = await asyncio.get_event_loop().run_in_executor(
                    None, kill_roblox
                )
                await websocket.send(json.dumps({"ok": success, "message": message}))

            elif action == "ping":
                accounts = load_accounts()
                await websocket.send(json.dumps({
                    "ok": True,
                    "status": "alive",
                    "accounts": len(accounts),
                    "servers": len(all_targets()),
                }))

            else:
                await websocket.send(json.dumps({"ok": False, "error": "unknown action"}))
    finally:
        warn(f"Client disconnected: {remote}")


def run_ws_server():
    async def _serve():
        async with websockets.serve(ws_handler, WS_HOST, WS_PORT):
            await asyncio.Future()

    asyncio.run(_serve())


def main():
    if os.name != "nt":
        warn("This tool is designed for Windows.")
    threading.Thread(target=run_ws_server, daemon=True).start()
    info(f"Websocket control listening on ws://{WS_HOST}:{WS_PORT}")
    info('Send JSON like {"action":"launch","username":"YourAlt","server":"Farm 1"}')
    info('"server" is optional — omit it to use the first configured server for that account.')
    info('{"action":"kill"} closes Roblox and launches the next account in rotation.')
    info('{"action":"stop"} just closes Roblox with no relaunch.')
    info('{"action":"ping"} returns {"ok":true,"status":"alive","accounts":N,"servers":N} for health checks.')
    info('Install the Client to Autoexecute, if the script does not find your executor please manually copy the file.')

    while True:
        header("Roblox Account Manager | Grow A Garden 2 Guild Event Farmer")
        print("  1. Add account (browser login)")
        print("  2. List accounts")
        print("  3. Launch account")
        print("  4. Kill Roblox (and launch next account)")
        print("  5. Configure account (place / private server)")
        print("  6. Remove account")
        print("  7. Install Client.lua to autoexec")
        print("  8. Quit")
        choice = input("\n> ").strip()

        if choice == "1":
            add_account()
        elif choice == "2":
            list_accounts()
        elif choice == "3":
            names = list_accounts()
            if names:
                sel = input("Number to launch: ").strip()
                try:
                    username = names[int(sel) - 1]
                except (ValueError, IndexError):
                    err("Invalid choice.")
                    username = None
                if username:
                    servers = load_accounts()[username].get("servers", [])
                    server = None
                    if len(servers) > 1:
                        for i, s in enumerate(servers, 1):
                            print(f"  {i}. {s['label']}")
                        server = input("Which server? (blank for first): ").strip() or None
                    launch_account(username, server)
        elif choice == "4":
            kill_and_advance()
        elif choice == "5":
            configure_account()
        elif choice == "6":
            remove_account()
        elif choice == "7":
            install_client_lua()
        elif choice == "8":
            print("Bye!")
            sys.exit(0)
        else:
            warn("Not a valid option.")


if __name__ == "__main__":
    main()

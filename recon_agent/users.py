"""Accounts: who may sign in to the worklist and the source simulator, and with which role.

Username and password sign-in. The role belongs to the account (set here, by whoever runs the
command), never chosen on the sign-in screen. Accounts live in one JSON file (RECON_USERS_FILE,
default output/users.json) with PBKDF2 password hashes.

    python -m recon_agent users seed                  # the demo accounts (DEMO_USERS), new passwords
    python -m recon_agent users add pat.lee --name "Pat Lee" --role planner
    python -m recon_agent users passwd pat.lee
    python -m recon_agent users role pat.lee manager
    python -m recon_agent users remove pat.lee
    python -m recon_agent users list

When the worklist starts and the file does not exist yet, it seeds the demo accounts and prints
their passwords to the log once. RECON_DEMO_PASSWORD, if set, is used for every demo account.
Changing a password or removing an account ends that person's open sessions.
"""
from __future__ import annotations

import datetime as dt
import getpass
import json
import os
import re
import secrets
import sys
import threading
from pathlib import Path

from . import config, security

ROLES = ("planner", "supervisor", "manager")
MIN_PASSWORD = 10
USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{1,39}")
# Dummy accounts for demos and acceptance testing: two of each working role and one manager.
DEMO_USERS = (
    ("planner1", "Planner One", "planner"),
    ("planner2", "Planner Two", "planner"),
    ("supervisor1", "Supervisor One", "supervisor"),
    ("supervisor2", "Supervisor Two", "supervisor"),
    ("manager1", "Manager One", "manager"),
)

_lock = threading.RLock()
_cache: dict = {"key": None, "users": {}}


class UserError(ValueError):
    """An account request that cannot be carried out (unknown user, weak password, ...)."""


def _path(path: Path | None) -> Path:
    return Path(path or config.USERS_FILE)


def load(path: Path | None = None) -> dict[str, dict]:
    """username -> {"name", "role", "password", "session"}. Re-read only when the file changes."""
    p = _path(path)
    try:
        st = p.stat()
    except FileNotFoundError:
        return {}
    key = (str(p), st.st_mtime_ns, st.st_size)
    with _lock:
        if _cache["key"] != key:
            _cache["users"] = json.loads(p.read_text(encoding="utf-8")).get("users", {})
            _cache["key"] = key
        return _cache["users"]


def _save(users: dict[str, dict], path: Path | None = None) -> None:
    security.write_json(_path(path), {"users": users}, mode=0o600, indent=2)   # owner only: password hashes


def get(username: str, path: Path | None = None) -> dict | None:
    return load(path).get((username or "").strip().lower())


def authenticate(username: str, password: str, path: Path | None = None) -> dict | None:
    """The account when the password is right, else None (same time either way)."""
    user = get(username, path)
    if user is None:
        if _cache.get("dummy") is None:
            _cache["dummy"] = security.hash_password(secrets.token_urlsafe(16))
        security.verify_password(password, _cache["dummy"])   # an unknown name takes as long as a wrong password
        return None
    return user if security.verify_password(password, user["password"]) else None


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise UserError(f"Use at least {MIN_PASSWORD} characters for a password.")


def add(username: str, name: str, role: str, password: str, path: Path | None = None) -> dict:
    username = username.strip().lower()
    if not USERNAME.fullmatch(username):
        raise UserError("A username is 2-40 lowercase letters, digits, dots, dashes or underscores.")
    if role not in ROLES:
        raise UserError(f"Role must be one of {', '.join(ROLES)}.")
    check_password(password)
    with _lock:
        users = dict(load(path))
        if username in users:
            raise UserError(f"{username} already exists.")
        users[username] = {"username": username, "name": (name or username).strip()[:40], "role": role,
                           "password": security.hash_password(password), "session": 1,
                           "created": dt.datetime.now().isoformat(timespec="seconds")}
        _save(users, path)
        return users[username]


def _change(username: str, path: Path | None, **fields) -> dict:
    username = username.strip().lower()
    with _lock:
        users = dict(load(path))
        if username not in users:
            raise UserError(f"No account {username}.")
        users[username] = {**users[username], **fields}
        _save(users, path)
        return users[username]


def set_password(username: str, password: str, path: Path | None = None) -> dict:
    check_password(password)
    user = get(username, path)
    if not user:
        raise UserError(f"No account {username}.")
    return _change(username, path, password=security.hash_password(password), session=user["session"] + 1)


def set_role(username: str, role: str, path: Path | None = None) -> dict:
    if role not in ROLES:
        raise UserError(f"Role must be one of {', '.join(ROLES)}.")
    return _change(username, path, role=role)


def remove(username: str, path: Path | None = None) -> None:
    username = username.strip().lower()
    with _lock:
        users = dict(load(path))
        if users.pop(username, None) is None:
            raise UserError(f"No account {username}.")
        _save(users, path)


def seed_demo(path: Path | None = None, password: str | None = None) -> list[tuple[str, str, str, str]]:
    """Create the demo accounts that do not exist yet. Returns (username, name, role, password)."""
    password = password if password is not None else os.environ.get("RECON_DEMO_PASSWORD", "")
    created = []
    for username, name, role in DEMO_USERS:
        if get(username, path):
            continue
        pw = password or secrets.token_urlsafe(9)
        add(username, name, role, pw, path)
        created.append((username, name, role, pw))
    return created


def ensure_accounts(path: Path | None = None) -> None:
    """At startup: seed the demo accounts when there is no accounts file yet, and say so."""
    if _path(path).exists():
        if not load(path):
            print(f"No accounts in {_path(path)}: nobody can sign in. Add one with "
                  "`python -m recon_agent users add`.")
        return
    print_created(seed_demo(path), _path(path))


def print_created(created: list[tuple[str, str, str, str]], where: Path) -> None:
    if not created:
        print("Every demo account already exists; nothing to add.")
        return
    print(f"Demo accounts written to {where}. These passwords are shown once; change them with "
          "`python -m recon_agent users passwd USERNAME`.")
    for username, name, role, pw in created:
        print(f"  {username:<12} {role:<10} {pw}   ({name})")


# --- command line --------------------------------------------------------------------

def _ask_password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise UserError("The passwords do not match.")
    return first


def cmd_users(args) -> None:
    try:
        if args.action == "list":
            users = load()
            if not users:
                print(f"No accounts in {config.USERS_FILE}.")
            for u in sorted(users.values(), key=lambda u: (ROLES.index(u["role"]), u["username"])):
                print(f"  {u['username']:<20} {u['role']:<10} {u['name']}")
        elif args.action == "seed":
            print_created(seed_demo(), config.USERS_FILE)
        elif args.action == "add":
            add(args.username, args.name or args.username, args.role, _ask_password(args))
            print(f"Added {args.username} ({args.role}).")
        elif args.action == "passwd":
            set_password(args.username, _ask_password(args))
            print(f"Password changed for {args.username}; their open sessions have ended.")
        elif args.action == "role":
            set_role(args.username, args.role)
            print(f"{args.username} is now a {args.role}.")
        elif args.action == "remove":
            remove(args.username)
            print(f"Removed {args.username}; their open sessions have ended.")
    except UserError as e:
        sys.exit(str(e))


def add_parser(sub) -> None:
    p = sub.add_parser("users", help="Manage sign-in accounts (list, seed, add, passwd, role, remove)")
    actions = p.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="List accounts")
    actions.add_parser("seed", help="Create the demo accounts that are missing")
    a = actions.add_parser("add", help="Add an account (prompts for the password)")
    a.add_argument("username")
    a.add_argument("--name", help="Name shown on claims and decisions (default: the username)")
    a.add_argument("--role", choices=ROLES, required=True)
    a.add_argument("--password-stdin", action="store_true", help="Read the password from standard input")
    a = actions.add_parser("passwd", help="Change a password (ends the account's open sessions)")
    a.add_argument("username")
    a.add_argument("--password-stdin", action="store_true", help="Read the password from standard input")
    a = actions.add_parser("role", help="Change an account's role")
    a.add_argument("username")
    a.add_argument("role", choices=ROLES)
    a = actions.add_parser("remove", help="Remove an account (ends its open sessions)")
    a.add_argument("username")
    p.set_defaults(func=cmd_users)

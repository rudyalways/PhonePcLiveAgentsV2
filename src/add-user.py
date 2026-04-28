#!/usr/bin/env python3
"""
Manage Sutando users.

Usage:
  python3 src/add-user.py <username> <secret>           # add user
  python3 src/add-user.py <username> <secret> --update  # change password
  python3 src/add-user.py --list                         # list users
  python3 src/add-user.py <username> --delete            # delete user
"""

import hashlib
import json
import sys
from pathlib import Path

USERS_FILE = Path(__file__).resolve().parent / "users.json"


def load_users() -> dict:
    data = json.loads(USERS_FILE.read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def save_users(users: dict) -> None:
    data = {
        "_comment": "Managed by src/add-user.py. Secrets are SHA-256 hashed.",
        "_example": "python3 src/add-user.py alice mysecret",
    }
    data.update(users)
    USERS_FILE.write_text(json.dumps(data, indent=2) + "\n")


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def main():
    args = sys.argv[1:]

    if not args or args == ["--list"]:
        users = load_users()
        if not users:
            print("No users configured.")
        else:
            print(f"{'Username':<20} {'Room'}")
            print("-" * 40)
            for name, info in users.items():
                print(f"{name:<20} {info['room']}")
        return

    if len(args) >= 2 and args[1] == "--delete":
        username = args[0]
        users = load_users()
        if username not in users:
            print(f"Error: user '{username}' not found")
            sys.exit(1)
        del users[username]
        save_users(users)
        print(f"Deleted user '{username}'")
        return

    if len(args) < 2:
        print(__doc__)
        sys.exit(1)

    username, secret = args[0], args[1]
    update = "--update" in args

    if not username.isalnum() or len(username) > 32:
        print("Error: username must be alphanumeric, max 32 chars")
        sys.exit(1)
    if len(secret) < 6:
        print("Error: secret must be at least 6 characters")
        sys.exit(1)

    users = load_users()

    if username in users and not update:
        print(f"Error: user '{username}' already exists. Use --update to change password.")
        sys.exit(1)

    users[username] = {
        "secret_sha256": hash_secret(secret),
        "room": f"sutando-{username}",
    }
    save_users(users)

    action = "Updated" if update else "Added"
    print(f"{action} user '{username}' → room 'sutando-{username}'")


if __name__ == "__main__":
    main()

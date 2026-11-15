"""Create or update an account in the tracker's user database.

Usage::

    python tools/add_user.py carol            # asks for the password
    python tools/add_user.py --list

The password is read with ``getpass`` (not echoed, not stored in the shell
history) and saved as a salted PBKDF2-SHA256 hash.
"""

import argparse
import getpass
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chat.accounts import USERNAME_PATTERN, UserStore  # noqa: E402

DEFAULT_USERS = Path(__file__).resolve().parent.parent / "db" / "users.json"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("username", nargs="?")
    parser.add_argument("--users", default=str(DEFAULT_USERS))
    parser.add_argument("--list", action="store_true", help="list accounts")
    args = parser.parse_args()
    store = UserStore(args.users)
    if args.list or not args.username:
        print("\n".join(store.usernames()) or "(no accounts)")
        sys.exit(0)
    if not re.fullmatch(USERNAME_PATTERN, args.username):
        sys.exit("Username: 1-32 letters, digits, '_', '.', '-'")
    password = getpass.getpass("Password for {}: ".format(args.username))
    if len(password) < 6 or password != getpass.getpass("Repeat: "):
        sys.exit("Passwords differ or are shorter than 6 characters")
    store.add(args.username, password)
    print("Saved {} in {}".format(args.username, args.users))

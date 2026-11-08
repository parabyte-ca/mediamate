"""Backups keep secrets encrypted with a passphrase.

Before a backup leaves the server, a copy of the database has every secret setting
(passwords, API keys, tokens, the session secret, the VAPID private key) and every
indexer download link (they can hold API keys and tracker passkeys) replaced with
``enc:<Fernet token>``. The key comes from the passphrase (PBKDF2-HMAC-SHA256, random
salt). The copy is VACUUMed so no old value lingers in free pages. Restore reverses it."""
import base64
import os
import sqlite3
import tempfile

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .secret_settings import SECRET_KEYS, SERVER_ONLY_KEYS

ITERATIONS = 390_000
PREFIX = "enc:"
CHECK = "mediamate-backup"
MIN_PASSPHRASE = 8
SECRETS = SECRET_KEYS | SERVER_ONLY_KEYS | {"tpdb_api_key"}


class WrongPassphrase(Exception):
    pass


def _fernet(passphrase: str, salt: bytes, iterations: int = ITERATIONS) -> Fernet:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return Fernet(base64.urlsafe_b64encode(kdf.derive(passphrase.encode())))


def _tables(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _rewrite(con, fn) -> None:
    """Apply fn to every secret value in the database copy."""
    tables = _tables(con)
    if "settings" in tables:
        marks = ",".join("?" * len(SECRETS))
        for key, value in con.execute(f"SELECT key, value FROM settings WHERE key IN ({marks})", sorted(SECRETS)).fetchall():
            if value:
                con.execute("UPDATE settings SET value=? WHERE key=?", (fn(value), key))
    if "downloads" in tables:
        for rid, link in con.execute("SELECT id, magnet_link FROM downloads WHERE magnet_link LIKE 'http%' "
                                     "OR magnet_link LIKE ?", (PREFIX + "%",)).fetchall():
            con.execute("UPDATE downloads SET magnet_link=? WHERE id=?", (fn(link), rid))
    if "indexer_links" in tables:
        con.execute("DELETE FROM indexer_links")            # short-lived; never worth backing up


def seal(db_path: str, passphrase: str) -> tuple[bytes, dict, dict]:
    """(database bytes, metadata, settings snapshot) with every secret encrypted."""
    if len(passphrase or "") < MIN_PASSPHRASE:
        raise ValueError(f"Use a passphrase of at least {MIN_PASSPHRASE} characters")
    salt = os.urandom(16)
    f = _fernet(passphrase, salt)
    enc = lambda v: v if v.startswith(PREFIX) else PREFIX + f.encrypt(v.encode()).decode()  # noqa: E731
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, "copy.db")
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(copy)
        try:
            src.backup(dst)
        finally:
            src.close()
        try:
            _rewrite(dst, enc)
            dst.commit()
            dst.execute("PRAGMA journal_mode=DELETE")
            dst.execute("VACUUM")
            settings = dict(dst.execute("SELECT key, value FROM settings").fetchall()) \
                if "settings" in _tables(dst) else {}
        finally:
            dst.close()
        with open(copy, "rb") as fh:
            data = fh.read()
    meta = {"encrypted": True, "kdf": "pbkdf2-sha256", "iterations": ITERATIONS,
            "salt": base64.b64encode(salt).decode(), "check": f.encrypt(CHECK.encode()).decode()}
    return data, meta, settings


def is_sealed(meta: dict) -> bool:
    return bool(meta.get("encrypted"))


def unseal(data: bytes, meta: dict, passphrase: str) -> bytes:
    """The database bytes with secrets decrypted. Raises WrongPassphrase."""
    try:
        f = _fernet(passphrase or "", base64.b64decode(meta["salt"]), int(meta.get("iterations", ITERATIONS)))
        if f.decrypt(meta["check"].encode()).decode() != CHECK:
            raise WrongPassphrase()
    except (InvalidToken, KeyError, ValueError):
        raise WrongPassphrase()
    dec = lambda v: f.decrypt(v[len(PREFIX):].encode()).decode() if v.startswith(PREFIX) else v  # noqa: E731
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "restore.db")
        with open(path, "wb") as fh:
            fh.write(data)
        con = sqlite3.connect(path)
        try:
            _rewrite(con, dec)
            con.commit()
        except InvalidToken:
            raise WrongPassphrase()
        finally:
            con.close()
        with open(path, "rb") as fh:
            return fh.read()

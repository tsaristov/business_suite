"""Email connectivity — read-only IMAP across multiple accounts.

Deliberately named mailbox.py (not email.py) so it never shadows the Python stdlib
`email` package it relies on. Account metadata (label/host/port/username/ssl) lives in
data.json; the password is stored in the OS keyring (macOS Keychain) under the service
"business_suite_email", keyed by the account label — it is never written to disk here.

Read-only by design: mailboxes are opened with readonly=True so viewing does not mark
messages as seen. Heavy/optional imports (keyring) and stdlib imap/email are imported
lazily inside functions.
"""

import json
import os
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_HERE, "data.json")
_SERVICE = "business_suite_email"


# --------------------------------------------------------------------------- #
# Persistence (metadata only — no secrets)
# --------------------------------------------------------------------------- #
def load():
    if not os.path.exists(_DATA) or os.path.getsize(_DATA) == 0:
        return {"accounts": []}
    try:
        with open(_DATA) as f:
            data = json.load(f)
        data.setdefault("accounts", [])
        return data
    except (json.JSONDecodeError, OSError):
        return {"accounts": []}


def save(data):
    with open(_DATA, "w") as f:
        json.dump(data, f, indent=2)


def _find(data, label):
    return next((a for a in data["accounts"] if a["label"] == label), None)


# --------------------------------------------------------------------------- #
# Keyring helpers
# --------------------------------------------------------------------------- #
def _set_secret(label, password):
    import keyring
    keyring.set_password(_SERVICE, label, password)


def _get_secret(label):
    import keyring
    return keyring.get_password(_SERVICE, label)


def _del_secret(label):
    try:
        import keyring
        keyring.delete_password(_SERVICE, label)
    except Exception:
        pass  # nothing stored / backend missing — nothing to clean up


# --------------------------------------------------------------------------- #
# Account management
# --------------------------------------------------------------------------- #
def add_account(label, host, port=993, username="", password="", use_ssl=True):
    """Add (or update) an IMAP account. Password goes to the OS keyring, not data.json."""
    label = str(label or "").strip()
    host = str(host or "").strip()
    username = str(username or "").strip()
    if not (label and host and username):
        return {"ok": False, "error": "label, host, and username are required"}
    if not password:
        return {"ok": False, "error": "password is required (use an app password)"}
    try:
        port = int(port or 993)
    except (TypeError, ValueError):
        port = 993
    data = load()
    meta = {"label": label, "host": host, "port": port,
            "username": username, "use_ssl": bool(use_ssl)}
    existing = _find(data, label)
    if existing:
        existing.update(meta)
    else:
        data["accounts"].append(meta)
    try:
        _set_secret(label, password)
    except Exception as e:
        return {"ok": False, "error": f"could not store password in keyring: {e}"}
    save(data)
    return {"ok": True, "account": meta}


def remove_account(label):
    data = load()
    before = len(data["accounts"])
    data["accounts"] = [a for a in data["accounts"] if a["label"] != label]
    if len(data["accounts"]) == before:
        return {"ok": False, "error": "no such account"}
    _del_secret(label)
    save(data)
    return {"ok": True, "removed": label}


def list_accounts():
    """Configured accounts — metadata only, never the password."""
    return {"ok": True, "accounts": load()["accounts"]}


# --------------------------------------------------------------------------- #
# IMAP connection + parsing
# --------------------------------------------------------------------------- #
def _connect(label):
    """Open + log in to an account. Returns (imap, None) or (None, error_dict)."""
    import imaplib
    data = load()
    acct = _find(data, label)
    if not acct:
        return None, {"ok": False, "error": f"no account '{label}'"}
    password = _get_secret(label)
    if not password:
        return None, {"ok": False, "error": "no stored password for this account"}
    try:
        if acct.get("use_ssl", True):
            imap = imaplib.IMAP4_SSL(acct["host"], acct["port"])
        else:
            imap = imaplib.IMAP4(acct["host"], acct["port"])
        imap.login(acct["username"], password)
    except Exception as e:
        return None, {"ok": False, "error": f"login failed: {e}"}
    return imap, None


def _decode_header(value):
    from email.header import decode_header
    if not value:
        return ""
    parts = []
    for text, enc in decode_header(value):
        if isinstance(text, bytes):
            parts.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts).strip()


def _header_summary(uid, raw_flags, header_bytes):
    import email
    import imaplib
    msg = email.message_from_bytes(header_bytes)
    flags = imaplib.ParseFlags(raw_flags) if raw_flags else ()
    seen = any(b"\\Seen" == f or f == "\\Seen" for f in flags)
    return {
        "uid": uid,
        "subject": _decode_header(msg.get("Subject")) or "(no subject)",
        "from": _decode_header(msg.get("From")),
        "date": _decode_header(msg.get("Date")),
        "unread": not seen,
    }


def _extract_body(msg):
    """Prefer text/plain; fall back to stripped text/html."""
    if msg.is_multipart():
        plain, html = None, None
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp:
                continue
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                continue
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if ctype == "text/plain" and plain is None:
                plain = text
            elif ctype == "text/html" and html is None:
                html = text
        if plain is not None:
            return plain.strip()
        if html is not None:
            import re
            return re.sub(r"<[^>]+>", " ", html).strip()
        return ""
    payload = msg.get_payload(decode=True)
    if payload is None:
        return ""
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace").strip()


# --------------------------------------------------------------------------- #
# Read operations
# --------------------------------------------------------------------------- #
def list_emails(account, folder="INBOX", limit=20, unread_only=False):
    """List recent messages (newest first) as header summaries."""
    imap, err = _connect(account)
    if err:
        return err
    try:
        try:
            limit = max(1, min(int(limit or 20), 100))
        except (TypeError, ValueError):
            limit = 20
        imap.select(folder, readonly=True)
        criteria = "UNSEEN" if unread_only else "ALL"
        typ, data = imap.uid("search", None, criteria)
        if typ != "OK":
            return {"ok": False, "error": "search failed"}
        uids = data[0].split()
        uids = uids[-limit:][::-1]  # newest first
        messages = []
        for uid_b in uids:
            uid = uid_b.decode()
            typ, msg_data = imap.uid("fetch", uid, "(FLAGS RFC822.HEADER)")
            if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            raw_meta, header_bytes = msg_data[0][0], msg_data[0][1]
            messages.append(_header_summary(uid, raw_meta, header_bytes))
        return {"ok": True, "account": account, "folder": folder,
                "count": len(messages), "messages": messages}
    except Exception as e:
        return {"ok": False, "error": f"could not list emails: {e}"}
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def read_email(account, uid, folder="INBOX"):
    """Fetch one message's full headers + body by UID."""
    if not str(uid or "").strip():
        return {"ok": False, "error": "uid is required"}
    imap, err = _connect(account)
    if err:
        return err
    try:
        import email
        imap.select(folder, readonly=True)
        typ, msg_data = imap.uid("fetch", str(uid), "(RFC822)")
        if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
            return {"ok": False, "error": "no such message"}
        msg = email.message_from_bytes(msg_data[0][1])
        return {
            "ok": True,
            "account": account,
            "uid": str(uid),
            "subject": _decode_header(msg.get("Subject")) or "(no subject)",
            "from": _decode_header(msg.get("From")),
            "to": _decode_header(msg.get("To")),
            "date": _decode_header(msg.get("Date")),
            "body": _extract_body(msg),
        }
    except Exception as e:
        return {"ok": False, "error": f"could not read email: {e}"}
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def search_emails(account, query, folder="INBOX", limit=20):
    """Full-text IMAP search; returns header summaries (newest first)."""
    if not str(query or "").strip():
        return {"ok": False, "error": "query is required"}
    imap, err = _connect(account)
    if err:
        return err
    try:
        try:
            limit = max(1, min(int(limit or 20), 100))
        except (TypeError, ValueError):
            limit = 20
        imap.select(folder, readonly=True)
        typ, data = imap.uid("search", None, "TEXT", f'"{query}"')
        if typ != "OK":
            return {"ok": False, "error": "search failed"}
        uids = data[0].split()[-limit:][::-1]
        messages = []
        for uid_b in uids:
            uid = uid_b.decode()
            typ, msg_data = imap.uid("fetch", uid, "(FLAGS RFC822.HEADER)")
            if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            messages.append(_header_summary(uid, msg_data[0][0], msg_data[0][1]))
        return {"ok": True, "account": account, "query": query,
                "count": len(messages), "messages": messages}
    except Exception as e:
        return {"ok": False, "error": f"search failed: {e}"}
    finally:
        try:
            imap.logout()
        except Exception:
            pass

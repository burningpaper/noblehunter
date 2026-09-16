"""Encrypting the Gmail refresh tokens Noble Hunter stores.

A refresh token is a long-lived key to someone's mailbox, so it never goes in the database in
readable form. `MAIL_TOKEN_KEY` (a Fernet key, the same value on Vercel and the Mac Mini)
encrypts it; without that key the app still runs and simply says mail isn't configured.

Fernet gives authenticated encryption, so a token altered in the database fails to decrypt
rather than being used. The key itself lives only in the environment, never in the database.
"""

from cryptography.fernet import Fernet, InvalidToken

KEY_NAME = "MAIL_TOKEN_KEY"


class MailNotConfigured(RuntimeError):
    """No usable mail key, or a stored token this key can't read."""


def mail_cipher(key: str | None) -> Fernet:
    """The cipher for `key`, or raise if mail isn't configured on this machine."""
    if key is None or not key.strip():
        raise MailNotConfigured(
            f"{KEY_NAME} is not set, so Noble Hunter can't store Gmail access. "
            'Generate one with `python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"` and add it to the environment.'
        )
    try:
        return Fernet(key.strip().encode())
    except (ValueError, TypeError) as error:
        raise MailNotConfigured(f"{KEY_NAME} is not a valid Fernet key") from error


def encrypt_token(cipher: Fernet, token: str) -> str:
    """The stored form of a refresh token."""
    return cipher.encrypt(token.encode()).decode()


def decrypt_token(cipher: Fernet, stored: str) -> str:
    """The refresh token back, or raise if this key can't read it (rotated key, altered row)."""
    try:
        return cipher.decrypt(stored.encode()).decode()
    except (InvalidToken, ValueError) as error:
        raise MailNotConfigured(
            "A stored Gmail token couldn't be read with this MAIL_TOKEN_KEY. "
            "Reconnect the mailbox to store a fresh one."
        ) from error

"""Refresh tokens are encrypted at rest: without MAIL_TOKEN_KEY, mail isn't configured at all."""

import pytest
from cryptography.fernet import Fernet

from core.mail_crypto import MailNotConfigured, decrypt_token, encrypt_token, mail_cipher

TOKEN = "1//0gFAKE-refresh-token_value"


def test_a_token_survives_a_round_trip():
    cipher = mail_cipher(Fernet.generate_key().decode())

    assert decrypt_token(cipher, encrypt_token(cipher, TOKEN)) == TOKEN


def test_the_stored_form_does_not_contain_the_token():
    cipher = mail_cipher(Fernet.generate_key().decode())

    assert TOKEN not in encrypt_token(cipher, TOKEN)


def test_another_key_cannot_read_it():
    stored = encrypt_token(mail_cipher(Fernet.generate_key().decode()), TOKEN)
    other = mail_cipher(Fernet.generate_key().decode())

    with pytest.raises(MailNotConfigured, match="couldn't be read"):
        decrypt_token(other, stored)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_without_a_key_mail_is_not_configured(value):
    with pytest.raises(MailNotConfigured, match="MAIL_TOKEN_KEY"):
        mail_cipher(value)


def test_a_key_that_is_not_a_fernet_key_says_so():
    with pytest.raises(MailNotConfigured, match="MAIL_TOKEN_KEY"):
        mail_cipher("not-a-real-key")


def test_a_generated_key_is_usable():
    # What the setup instructions tell Jarred to run.
    key = Fernet.generate_key().decode()

    assert decrypt_token(mail_cipher(key), encrypt_token(mail_cipher(key), TOKEN)) == TOKEN

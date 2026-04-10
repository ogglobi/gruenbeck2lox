"""Azure B2C OAuth2 PKCE authentication for myGruenbeck cloud.

This module implements the login flow used by the official Gruenbeck app.
Credentials are accepted as parameters and are never written to disk or logs.

Reference: https://github.com/TA2k/ioBroker.gruenbeck
Reference: https://github.com/hoep/symcon-gruenbeck
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Azure B2C / myGruenbeck constants
_TENANT_ID   = "a50d35c1-202f-4da7-aa87-76e51a3098c6"
_POLICY      = "B2C_1A_SignInUp"
_CLIENT_ID   = "5a83cc16-ffb1-42e9-9859-9fbf07f36df8"
_REDIRECT    = "msal5a83cc16-ffb1-42e9-9859-9fbf07f36df8://auth"
_SCOPE       = (
    "https://gruenbeckb2c.onmicrosoft.com/iot/user_impersonation "
    "openid profile offline_access"
)
_B2C_BASE    = f"https://gruenbeckb2c.b2clogin.com/{_TENANT_ID}"
_AUTHORIZE   = f"{_B2C_BASE}/{_POLICY}/oauth2/v2.0/authorize"
_TOKEN_URL   = f"{_B2C_BASE}/{_POLICY}/oauth2/v2.0/token"

_USER_AGENT  = "Gruenbeck/354 CFNetwork/1240.0.4 Darwin/20.6.0"
_APP_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept-Language": "de-de",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


@dataclass
class TokenSet:
    """Holds access + refresh tokens obtained from Azure B2C."""

    access_token: str
    refresh_token: str


def _pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier / code_challenge pair (S256 method)."""
    while True:
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        if "+" not in verifier + challenge and "/" not in verifier + challenge:
            return verifier, challenge


def _extract(text: str, key: str) -> str:
    """Extract a JSON string value by key from an HTML page (simple regex)."""
    m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', text)
    if not m:
        raise ValueError(f"Azure B2C response missing field: {key}")
    return m.group(1)


def _cookies_from_headers(set_cookie_list: list[str]) -> str:
    """Build a Cookie header value from Set-Cookie response headers."""
    return "; ".join(h.split(";")[0] for h in set_cookie_list)


async def login(email: str, password: str) -> TokenSet:
    """Authenticate against myGruenbeck (Azure B2C) and return a TokenSet.

    Args:
        email: myGruenbeck account e-mail.
        password: myGruenbeck account password - never logged.

    Returns:
        TokenSet containing access_token and refresh_token.

    Raises:
        RuntimeError: If any authentication step fails.
    """
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)

    # Step 1: GET authorize URL - use urllib.parse.urlencode for correct encoding
    authorize_url = _AUTHORIZE + "?" + urllib.parse.urlencode({
        "x-client-Ver": "0.8.0",
        "state": state,
        "client_info": "1",
        "response_type": "code",
        "code_challenge_method": "S256",
        "x-app-name": "Gruenbeck",
        "x-client-OS": "14.3",
        "x-app-ver": "1.2.1",
        "scope": _SCOPE,
        "x-client-SKU": "MSAL.iOS",
        "code_challenge": challenge,
        "x-client-CPU": "64",
        "client-request-id": secrets.token_hex(16).upper(),
        "redirect_uri": _REDIRECT,
        "client_id": _CLIENT_ID,
        "haschrome": "1",
        "return-client-request-id": "true",
        "x-client-DM": "iPhone",
    })

    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0, headers=_APP_HEADERS) as c:
        resp1 = await c.get(authorize_url)

    if resp1.status_code >= 400:
        raise RuntimeError(f"Azure B2C authorize failed: {resp1.status_code}")

    html = resp1.text
    try:
        csrf     = _extract(html, "csrf")
        trans_id = _extract(html, "transId")
        policy   = _extract(html, "policy")
        tenant   = _extract(html, "tenant")
    except ValueError as exc:
        raise RuntimeError(f"Azure B2C step 1 parse error: {exc}") from exc

    logger.debug("Azure B2C step 1 OK (transId=%s)", trans_id)

    # Step 2: POST credentials to SelfAsserted
    cookies1 = _cookies_from_headers(resp1.headers.get_list("set-cookie"))
    self_asserted_url = (
        f"https://gruenbeckb2c.b2clogin.com{tenant}"
        f"/SelfAsserted?tx={trans_id}&p={policy}"
    )
    step2_body = urllib.parse.urlencode({
        "request_type": "RESPONSE",
        "signInName": email,
        "password": password,
    })

    async with httpx.AsyncClient(follow_redirects=False, timeout=20.0) as c:
        resp2 = await c.post(
            self_asserted_url,
            content=step2_body.encode(),
            headers={
                **_APP_HEADERS,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-CSRF-TOKEN": csrf,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": "https://gruenbeckb2c.b2clogin.com",
                "Cookie": cookies1,
            },
        )

    if resp2.status_code >= 400:
        raise RuntimeError(f"Azure B2C credentials rejected: {resp2.status_code}")

    try:
        body_data = resp2.json()
        status_val = body_data.get("status")
        if status_val not in ("200", 200, None):
            msg = body_data.get("message", "Unknown error")
            raise RuntimeError(f"myGruenbeck login failed: {msg}")
    except (ValueError, KeyError):
        pass

    logger.debug("Azure B2C step 2 OK")

    # Step 3: GET confirmed - MUST NOT follow redirects (302 carries the auth code)
    cookies2_list = resp1.headers.get_list("set-cookie") + resp2.headers.get_list("set-cookie")
    cookies2 = _cookies_from_headers(cookies2_list)
    if f"x-ms-cpim-csrf={csrf}" not in cookies2:
        cookies2 += f"; x-ms-cpim-csrf={csrf}"

    confirmed_url = (
        f"https://gruenbeckb2c.b2clogin.com{tenant}"
        f"/api/CombinedSigninAndSignup/confirmed"
        f"?csrf_token={csrf}&tx={trans_id}&p={policy}"
    )

    async with httpx.AsyncClient(follow_redirects=False, timeout=20.0) as c:
        resp3 = await c.get(confirmed_url, headers={**_APP_HEADERS, "Cookie": cookies2})

    location = resp3.headers.get("location", "")
    logger.debug("Azure B2C step 3 status=%d location=%.120s", resp3.status_code, location)

    code_match = re.search(r"[?&]code=([^&]+)", location)
    if not code_match:
        code_match = re.search(r"code%3[Dd]([^&]+)", location)
    if not code_match:
        raise RuntimeError(
            f"Azure B2C confirmed step: authorization code not found. "
            f"Status={resp3.status_code}, Location={location[:200]!r}"
        )
    auth_code = urllib.parse.unquote(code_match.group(1))
    logger.debug("Azure B2C step 3 OK (code obtained)")

    # Step 4: Exchange code for tokens
    # Use the tenant path from step 1 HTML (includes the B2C policy segment)
    token_url = f"https://gruenbeckb2c.b2clogin.com{tenant}/oauth2/v2.0/token"
    step4_body = urllib.parse.urlencode({
        "client_info": "1",
        "scope": _SCOPE,
        "code": auth_code,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
        "redirect_uri": _REDIRECT,
        "client_id": _CLIENT_ID,
    })

    async with httpx.AsyncClient(follow_redirects=False, timeout=20.0) as c:
        resp4 = await c.post(
            token_url,
            content=step4_body.encode(),
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "client-request-id": secrets.token_hex(16).upper(),
                "x-ms-PkeyAuth": "1.0",
                "x-client-Ver": "0.8.0",
                "return-client-request-id": "true",
            },
        )

    if resp4.status_code >= 400:
        raise RuntimeError(f"Azure B2C token exchange failed: {resp4.status_code}")

    data = resp4.json()
    logger.info("myGruenbeck login successful")
    return TokenSet(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
    )


async def refresh_tokens(refresh_token: str) -> TokenSet:
    """Obtain a new TokenSet using an existing refresh_token.

    Args:
        refresh_token: A valid myGruenbeck refresh token.

    Returns:
        Fresh TokenSet.
    """
    body = urllib.parse.urlencode({
        "client_id": _CLIENT_ID,
        "scope": _SCOPE,
        "refresh_token": refresh_token,
        "client_info": "1",
        "grant_type": "refresh_token",
    })

    async with httpx.AsyncClient(follow_redirects=False, timeout=15.0) as c:
        resp = await c.post(
            _TOKEN_URL,
            content=body.encode(),
            headers={
                "User-Agent": _USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        logger.debug("myGruenbeck token refreshed")
        return TokenSet(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
        )

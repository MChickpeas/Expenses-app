import os

import jwt
from flask import abort, request

TEAM_DOMAIN = os.environ["CF_TEAM_DOMAIN"].rstrip("/")
AUDIENCE = os.environ["CF_ACCESS_AUD"]

# Downloads Cloudflare's public keys and caches them
_jwks = jwt.PyJWKClient(f"{TEAM_DOMAIN}/cdn-cgi/access/certs")


def verified_email():
    """Return the logged-in email, or reject the request if the token isn't genuine."""
    token = request.headers.get("Cf-Access-Jwt-Assertion")
    if not token:
        abort(401)  # no stamp at all
    try:
        key = _jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token, key,
            algorithms=["RS256"],
            audience=AUDIENCE,     # issued for THIS app
            issuer=TEAM_DOMAIN,    # issued by YOUR Cloudflare team
        )
    except jwt.PyJWTError:
        abort(403)  # forged, expired, or meant for another app
    return claims["email"].lower()
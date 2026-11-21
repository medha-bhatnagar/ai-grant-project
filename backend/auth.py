"""Sign-in providers. Each one answers the same question: 'who is this person?'
They return {'login_id', 'email', 'name'}. To switch providers, change AUTH_PROVIDER. Nothing else changes."""
import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx

from . import config


class AuthError(Exception):
    pass


CALLBACK = config.PUBLIC_URL + "/auth/callback"  # must match what UConn IT registers

# ---------- CAS: the older UConn NetID sign-in ----------
def cas_login_url():
    return f"{config.CAS_SERVER_URL}/login?service={quote(CALLBACK, safe='')}"


async def cas_identity(ticket):
    """After UConn's page signs the user in, it sends back a one-time ticket. We ask CAS if it is real."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{config.CAS_SERVER_URL}/serviceValidate",
                                params={"service": CALLBACK, "ticket": ticket})
    ns = {"cas": "http://www.yale.edu/tp/cas"}
    try:
        ok = ET.fromstring(resp.text).find("cas:authenticationSuccess", ns)
    except ET.ParseError:
        raise AuthError("CAS sent an answer we could not read")
    if ok is None:
        raise AuthError("CAS did not accept the ticket")
    login_id = ok.find("cas:user", ns).text.strip().lower()
    box, attrs = ok.find("cas:attributes", ns), {}
    if box is not None:  # which extra details CAS sends (name, email) depends on UConn's settings
        attrs = {el.tag.split("}")[-1]: (el.text or "").strip() for el in box}
    return {"login_id": login_id, "email": attrs.get("mail") or attrs.get("email") or "",
            "name": attrs.get("displayName") or attrs.get("cn") or login_id}


# ---------- OIDC: the newer sign-in (Microsoft Entra) ----------
oauth = None


def setup_oidc():
    global oauth
    from authlib.integrations.starlette_client import OAuth
    oauth = OAuth()
    oauth.register(name="uconn", server_metadata_url=config.OIDC_METADATA_URL,
                   client_id=config.OIDC_CLIENT_ID, client_secret=config.OIDC_CLIENT_SECRET,
                   client_kwargs={"scope": "openid profile email"})


async def oidc_login(request):
    return await oauth.uconn.authorize_redirect(request, CALLBACK)


async def oidc_identity(request):
    try:
        token = await oauth.uconn.authorize_access_token(request)
    except Exception as exc:
        raise AuthError(f"OIDC sign-in failed: {exc}")
    claims = token.get("userinfo") or {}
    login_id = (claims.get(config.OIDC_USER_CLAIM) or claims.get("email") or claims.get("sub") or "").lower()
    return {"login_id": login_id, "email": (claims.get("email") or claims.get("preferred_username") or "").lower(),
            "name": claims.get("name") or login_id}


async def send_guest_code(email, code, expires_minutes):
    if config.AUTH_PROVIDER == "demo":
        return False
    if not all((config.GRAPH_TENANT_ID, config.GRAPH_CLIENT_ID,
                config.GRAPH_CLIENT_SECRET, config.GRAPH_SENDER_EMAIL)):
        raise AuthError("Microsoft Graph email delivery is not configured")
    async with httpx.AsyncClient(timeout=10) as client:
        token_response = await client.post(
            f"https://login.microsoftonline.com/{config.GRAPH_TENANT_ID}/oauth2/v2.0/token",
            data={"client_id": config.GRAPH_CLIENT_ID, "client_secret": config.GRAPH_CLIENT_SECRET,
                  "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"},
        )
        token_response.raise_for_status()
        access_token = token_response.json().get("access_token")
        if not access_token:
            raise AuthError("Microsoft Graph did not return an access token")
        message = {
            "message": {
                "subject": "Grant application guest verification code",
                "body": {"contentType": "Text", "content": f"Your verification code is {code}. It expires in {expires_minutes} minutes."},
                "toRecipients": [{"emailAddress": {"address": email}}],
            },
            "saveToSentItems": "false",
        }
        response = await client.post(
            f"https://graph.microsoft.com/v1.0/users/{config.GRAPH_SENDER_EMAIL}/sendMail",
            headers={"Authorization": f"Bearer {access_token}"},
            json=message,
        )
        response.raise_for_status()
    return True

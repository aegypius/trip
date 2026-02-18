import logging
import secrets
from typing import Annotated

import jwt
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlmodel import select

logger = logging.getLogger(__name__)

from ..config import get_settings
from ..db.core import init_user_data
from ..deps import SessionDep, get_current_username
from ..models.models import (AuthParams, LoginRegisterModel, MagicLink,
                             PendingTOTP, Token, UpdateUserPassword, User)
from ..security import (create_access_token, create_tokens,
                        generate_totp_secret, get_oidc_client, get_oidc_config,
                        hash_password, verify_password, verify_totp_code)
from ..telemetry import record_exception
from ..utils.date import dt_utc, dt_utc_offset
from ..utils.utils import generate_filename

router = APIRouter(prefix="/api/auth", tags=["auth"])
pending_totp_usernames = {}


@router.get("/params", response_model=AuthParams)
async def auth_params(request: Request) -> AuthParams:
    """
    Get authentication parameters including OIDC authorization URL.
    
    Optionally generates PKCE (Proof Key for Code Exchange) parameters for secure OIDC flow:
    - Creates code_verifier and code_challenge (if PKCE enabled)
    - Stores verifier and state in httpOnly cookies
    - Returns authorization URL for the OIDC provider
    
    The secure flag is only set when both the OIDC provider and current request use HTTPS.
    This allows OIDC to work in development (HTTP) while being secure in production (HTTPS).
    
    PKCE can be disabled via OIDC_PKCE_ENABLED=false for legacy providers.
    """
    data = {"oidc": None, "register_enabled": get_settings().REGISTER_ENABLE}

    if not (get_settings().OIDC_CLIENT_ID and get_settings().OIDC_CLIENT_SECRET):
        return {"oidc": None, "register_enabled": get_settings().REGISTER_ENABLE}

    oidc_config = await get_oidc_config()
    auth_endpoint = oidc_config.get("authorization_endpoint")
    
    oidc_client = get_oidc_client()
    
    # Generate PKCE parameters if enabled (RFC 7636)
    code_verifier = None
    if get_settings().OIDC_PKCE_ENABLED:
        code_verifier = secrets.token_urlsafe(32)
        code_challenge = create_s256_code_challenge(code_verifier)
        uri, state = oidc_client.create_authorization_url(
            auth_endpoint,
            code_challenge=code_challenge,
            code_challenge_method='S256'
        )
    else:
        uri, state = oidc_client.create_authorization_url(auth_endpoint)
    
    data["oidc"] = uri

    response = JSONResponse(content=data)
    
    # Use secure cookies based on config or auto-detect from HTTPS usage
    if get_settings().COOKIE_SECURE:
        is_secure = True
    else:
        # Only use secure cookies when both endpoints are HTTPS
        oidc_is_https = "https://" in get_settings().OIDC_REDIRECT_URI
        request_is_https = request.url.scheme == "https"
        is_secure = oidc_is_https and request_is_https
    
    # Store OIDC state in cookie (60s expiration)
    response.set_cookie(
        "oidc_state", value=state, httponly=True, secure=is_secure, samesite="Lax", max_age=60
    )
    
    # Store code verifier if PKCE is enabled
    if code_verifier:
        response.set_cookie(
            "oidc_verifier", value=code_verifier, httponly=True, secure=is_secure, samesite="Lax", max_age=60
        )

    return response


@router.post("/oidc/login", response_model=Token)
async def oidc_login(
    session: SessionDep,
    code: str = Body(..., embed=True),
    state: str = Body(..., embed=True),
    oidc_state: str = Cookie(None),
    oidc_verifier: str = Cookie(None),
) -> Token:
    """
    Complete OIDC login flow by exchanging authorization code for tokens.
    
    Validates:
    - State parameter matches cookie (CSRF protection)
    - Code verifier is present (if PKCE enabled)
    - ID token signature and claims
    
    Creates user account on first login if user doesn't exist.
    
    Returns access and refresh tokens for the TRIP application.
    """
    if not (get_settings().OIDC_CLIENT_ID or get_settings().OIDC_CLIENT_SECRET):
        raise HTTPException(status_code=400, detail="Partial OIDC config")

    if not oidc_state or state != oidc_state:
        raise HTTPException(status_code=400, detail="OIDC login failed, invalid state")
    
    # Validate PKCE verifier if enabled
    if get_settings().OIDC_PKCE_ENABLED and not oidc_verifier:
        raise HTTPException(status_code=400, detail="OIDC login failed, missing verifier")

    oidc_config = await get_oidc_config()
    token_endpoint = oidc_config.get("token_endpoint")
    
    if not token_endpoint:
        raise HTTPException(status_code=500, detail="OIDC token_endpoint not found in discovery document")
    
    # Exchange authorization code for tokens (with PKCE verification if enabled)
    try:
        oidc_client = get_oidc_client()
<<<<<<< HEAD
        fetch_params = {
            "url": token_endpoint,
            "grant_type": "authorization_code",
            "code": code,
        }
        if get_settings().OIDC_PKCE_ENABLED:
            fetch_params["code_verifier"] = oidc_verifier
        
        token = oidc_client.fetch_token(**fetch_params)
    except Exception as e:
        record_exception(e)
        raise HTTPException(status_code=401, detail="OIDC token exchange failed")

    id_token = token.get("id_token")
    jwks_uri = oidc_config.get("jwks_uri")
    issuer = oidc_config.get("issuer")
    jwks_client = jwt.PyJWKClient(
        jwks_uri,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; TRIP/1 PyJWKClient; +https://github.com/itskovacs/trip)",
            "Accept": "application/json",
        },
    )

    try:
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        decoded = jwt.decode(
            id_token,
            key=signing_key.key,
            algorithms=["RS256"],
            audience=get_settings().OIDC_CLIENT_ID,
            issuer=issuer,
        )
    except Exception as exc:
        record_exception(exc)
        raise HTTPException(status_code=401, detail="Invalid ID token")

    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid ID token")

    username = decoded.get("preferred_username")
    if not username:
        raise HTTPException(status_code=401, detail="OIDC login failed, preferred_username missing")

    user = session.get(User, username)
    if not user:
        # TODO: password is non-null, we must init the pw with something, the model is not made for OIDC
        user = User(username=username, password=hash_password(generate_filename("find-something-else")))
        session.add(user)
        session.commit()
        init_user_data(session, username)

    return create_tokens(data={"sub": username})


@router.post("/login", response_model=Token | PendingTOTP)
def login(req: LoginRegisterModel, session: SessionDep) -> Token | PendingTOTP:
    if get_settings().OIDC_CLIENT_ID or get_settings().OIDC_CLIENT_SECRET:
        raise HTTPException(status_code=400, detail="OIDC is configured")

    db_user = session.get(User, req.username)
    if not db_user or not verify_password(req.password, db_user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if db_user.totp_enabled:
        pending_totp_secret = generate_totp_secret()  # A random code to track for verify fn
        pending_totp_usernames[db_user.username] = {
            "pending_code": pending_totp_secret,
            "exp": dt_utc_offset(5),
        }
        return {"pending_code": pending_totp_secret, "username": db_user.username}

    return create_tokens(data={"sub": db_user.username})


@router.post("/login_totp", response_model=Token)
async def login_verify_totp(
    session: SessionDep,
    username: str = Body(..., embed=True),
    pending_code: str = Body(..., embed=True),
    code: str = Body(..., embed=True),
) -> Token:
    user = session.get(User, username)
    if not user or not user.totp_enabled:
        raise HTTPException(status_code=401, detail="Invalid TOTP flow")

    record = pending_totp_usernames.get(username)
    if not record or record["exp"] < dt_utc() or record["pending_code"] != pending_code:
        pending_totp_usernames.pop(username, None)
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not verify_totp_code(user.totp_secret, code):
        raise HTTPException(status_code=403, detail="Invalid TOTP code")

    return create_tokens({"sub": user.username})


@router.post("/register", response_model=Token)
def register(req: LoginRegisterModel, session: SessionDep) -> Token:
    if get_settings().OIDC_CLIENT_ID or get_settings().OIDC_CLIENT_SECRET:
        raise HTTPException(status_code=400, detail="OIDC is configured")

    if not get_settings().REGISTER_ENABLE:
        if not req.magicToken:
            raise HTTPException(status_code=400, detail="Registration disabled")

        db_token = session.exec(select(MagicLink).where(MagicLink.token == req.magicToken)).first()
        if not db_token:
            raise HTTPException(status_code=404, detail="Invalid token: not found")

        if db_token.expires < dt_utc():
            session.delete(db_token)
            session.commit()
            raise HTTPException(status_code=404, detail="Invalid token: expired")
        session.delete(db_token)

    db_user = session.get(User, req.username)
    if db_user:
        raise HTTPException(status_code=409, detail="The resource already exists")

    existing_user = session.exec(select(User)).first()
    is_first_user = existing_user is None

    new_user = User(username=req.username, password=hash_password(req.password), is_admin=is_first_user)
    session.add(new_user)
    session.commit()
    if is_first_user:
        logger.critical(f"[Register] First user registered, {req.username} is admin")

    init_user_data(session, new_user.username)

    return create_tokens(data={"sub": new_user.username})


@router.post("/refresh")
def refresh_token(refresh_token: str = Body(..., embed=True)):
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Refresh token expected")

    try:
        payload = jwt.decode(refresh_token, get_settings().SECRET_KEY, algorithms=[get_settings().ALGORITHM])
        username = payload.get("sub", None)

        if not username:
            raise HTTPException(status_code=401, detail="Invalid Token")

        new_access_token = create_access_token(data={"sub": username})

        return {"access_token": new_access_token}

    except jwt.ExpiredSignatureError as e:
        record_exception(e)
        raise HTTPException(status_code=401, detail="Invalid Token")
    except jwt.PyJWTError as e:
        record_exception(e)
        raise HTTPException(status_code=401, detail="Invalid Token")


@router.post("/update_password")
async def update_password(
    data: UpdateUserPassword,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
):
    if get_settings().OIDC_CLIENT_ID and get_settings().OIDC_CLIENT_SECRET:
        raise HTTPException(status_code=400, detail="Bad request")

    db_user = session.get(User, current_user)
    if db_user.totp_enabled:
        if not data.code:
            raise HTTPException(status_code=400, detail="Bad request: TOTP missing")

        success = verify_totp_code(db_user.totp_secret, data.code)
        if not success:
            raise HTTPException(status_code=403, detail="Invalid code")

    if not verify_password(data.current, db_user.password):
        raise HTTPException(status_code=403, detail="Invalid credentials")

    db_user.password = hash_password(data.updated)
    session.add(db_user)
    session.commit()
    return {}

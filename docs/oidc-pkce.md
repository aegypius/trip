# OIDC Authentication with PKCE

TRIP supports OpenID Connect (OIDC) authentication with optional PKCE (Proof Key for Code Exchange) for enhanced security.

## Overview

The OIDC flow supports:
- **PKCE (RFC 7636)** - Prevents authorization code interception attacks (enabled by default)
- **Legacy OIDC providers** - PKCE can be disabled via configuration
- **Mixed HTTP/HTTPS environments** - Secure cookies only when appropriate
- **Detailed error logging** - All OIDC errors are captured in OpenTelemetry traces

## Configuration

### Required Settings

```yaml
# storage/config.yml
OIDC_DISCOVERY_URL: "https://provider.example.com/.well-known/openid-configuration"
OIDC_CLIENT_ID: "your-client-id"
OIDC_CLIENT_SECRET: "your-client-secret"
OIDC_REDIRECT_URI: "https://trip.example.com/auth/callback"  # Must match provider config
```

### Optional: Disable PKCE

For legacy OIDC providers that don't support PKCE:

```yaml
# storage/config.yml
OIDC_PKCE_ENABLED: false
```

Or via environment variable:

```bash
export OIDC_PKCE_ENABLED=false
```

**Note:** PKCE is strongly recommended for security. Only disable if your OIDC provider doesn't support it.

## Architecture

### 1. Authorization URL Generation (`GET /api/auth/params`)

**With PKCE enabled (default):**

```python
# Generate PKCE parameters
code_verifier = secrets.token_urlsafe(32)  # Random 256-bit value
code_challenge = create_s256_code_challenge(code_verifier)  # SHA-256 hash

# Create authorization URL with PKCE
uri, state = oidc_client.create_authorization_url(
    auth_endpoint,
    code_challenge=code_challenge,
    code_challenge_method='S256'
)

# Store state and verifier in httpOnly cookies
response.set_cookie("oidc_state", value=state, ...)
response.set_cookie("oidc_verifier", value=code_verifier, ...)
```

**With PKCE disabled:**

```python
# Create authorization URL without PKCE
uri, state = oidc_client.create_authorization_url(auth_endpoint)

# Store only state in httpOnly cookie
response.set_cookie("oidc_state", value=state, ...)
```

**Security features:**
- `code_verifier` stored in httpOnly cookie (not accessible to JavaScript)
- `state` parameter for CSRF protection
- Cookies only marked `secure` when both client and provider use HTTPS

### 2. Token Exchange (`POST /api/auth/oidc/login`)

```python
# Validate state (CSRF protection)
if not oidc_state or state != oidc_state:
    raise HTTPException(status_code=400, detail="Invalid state")

# Exchange code for tokens (with PKCE verifier if enabled)
fetch_params = {
    "grant_type": "authorization_code",
    "code": code,
}
if settings.OIDC_PKCE_ENABLED:
    fetch_params["code_verifier"] = oidc_verifier

token = oidc_client.fetch_token(token_endpoint, **fetch_params)

# Validate ID token (HS256 or RS256)
decoded = jwt.decode(id_token, ...)

# Create or retrieve user
user = session.get(User, username) or create_new_user()

# Return application tokens
return create_tokens(data={"sub": username})
```

### Supported ID Token Algorithms

- **HS256**: Symmetric signing with client secret
- **RS256**: Asymmetric signing with JWKS (fetched from provider)

The algorithm is auto-detected from the ID token header.

## HTTP/HTTPS Compatibility

The implementation handles mixed HTTP/HTTPS environments:

```python
# Only use secure cookies when BOTH are HTTPS
oidc_is_https = "https://" in settings.OIDC_REDIRECT_URI
request_is_https = request.url.scheme == "https"
is_secure = oidc_is_https and request_is_https
```

**Scenarios:**
- ✅ Dev (HTTP client → HTTPS provider): Works, cookies not secure
- ✅ Prod (HTTPS client → HTTPS provider): Works, cookies secure
- ❌ HTTP provider: Not recommended, OIDC providers should always use HTTPS

## Error Handling

All OIDC errors are logged and captured in OpenTelemetry traces:

```python
try:
    token = oidc_client.fetch_token(...)
except Exception as e:
    logger.error(f"OIDC token exchange failed: {e}")
    raise HTTPException(status_code=401, detail=f"OIDC token exchange failed: {str(e)}")
```

Check Jaeger UI for detailed error messages in the `http.response.body` attribute.

## Security Considerations

### PKCE Implementation

- **Code Verifier**: 32-byte random value (256 bits of entropy)
- **Code Challenge**: SHA-256 hash of verifier, base64url-encoded
- **Storage**: Verifier stored in httpOnly cookie (60s expiration)

This prevents:
- Authorization code interception (even if attacker intercepts the code, they can't exchange it without the verifier)
- CSRF attacks (state parameter validation)
- Cookie theft via JavaScript (httpOnly flag)

### User Creation

On first login, users are automatically created:

```python
if not user:
    # Auto-provision user from OIDC
    user = User(
        username=decoded.get("preferred_username"),
        password=hash_password(generate_random())  # Dummy password (OIDC-only user)
    )
    session.add(user)
    init_user_data(session, username)
```

## Debugging

### Enable Detailed Logging

```yaml
# storage/config.yml
OTEL_ENABLED: true
OTEL_CONSOLE_EXPORTER: true  # Print traces to console
```

### Check Logs

```bash
docker compose logs -f app | grep -i oidc
```

### Verify PKCE Parameters

Check the authorization URL contains:
- `code_challenge` parameter
- `code_challenge_method=S256` parameter

### Common Issues

| Error | Cause | Solution |
|-------|-------|----------|
| "Invalid code verifier" | Cookie not sent or expired | Ensure cookies enabled, check secure flag |
| "Invalid state" | State mismatch or missing | Check CSRF protection, verify cookie storage |
| "Invalid ID token" | Signature verification failed | Check client secret, verify JWKS endpoint |

## References

- [RFC 7636 - PKCE](https://tools.ietf.org/html/rfc7636)
- [OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html)
- [Authlib Documentation](https://docs.authlib.org/en/latest/)

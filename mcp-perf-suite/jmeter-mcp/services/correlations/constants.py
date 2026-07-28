"""
Constants and patterns for correlation analysis.

Shared across all correlation analysis modules.
"""

import re
from typing import Dict, Set

# === Regex Patterns ===

# Numeric ID: one or more digits
NUMERIC_ID_RE = re.compile(r"^\d+$")

# GUID: standard UUID format
GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)

# JWT: three base64url segments separated by dots
JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

# Email address
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

# JSON keys that likely contain IDs (for SOURCE extraction)
# Matches: id, _id, userId, user_id, prodId, prod_id, uuid, guid, etc.
ID_KEY_PATTERNS = re.compile(
    r"(^id$|_id$|Id$|ID$|^.*id$|^.*_id$|uuid|guid)", 
    re.IGNORECASE
)


# === Header Configuration ===

# Header suffixes that indicate correlation/transaction IDs
CORRELATION_HEADER_SUFFIXES = (
    "id", "_id", "uuid", "transactionid", "correlationid",
    "requestid", "traceid", "spanid",
)

# Headers to skip when looking for correlation IDs (source extraction)
SKIP_HEADERS_SOURCE: Set[str] = {
    "content-type", "content-length", "cache-control", "date", "expires",
    "etag", "accept", "accept-encoding", "accept-language", "connection",
    "host", "origin", "referer", "user-agent", "cookie", "set-cookie",
    "access-control-allow-origin", "access-control-allow-credentials",
    "vary", "server", "strict-transport-security", "x-content-type-options",
    "x-frame-options", "content-encoding", "content-security-policy",
}

# Headers to skip when looking for usages (request headers that are HTTP plumbing)
SKIP_HEADERS_USAGE: Set[str] = {
    "content-type", "content-length", "cache-control", "date", "expires",
    "accept", "accept-encoding", "accept-language", "connection",
    "host", "origin", "user-agent", "cookie",
    "priority", "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "sec-fetch-user",
    "upgrade-insecure-requests", "if-none-match", "if-modified-since",
    "x-requested-with", "dnt", "pragma",
    # Pseudo-headers
    ":authority", ":method", ":path", ":scheme",
}


# === Value Configuration ===

# Minimum length for numeric IDs to avoid false positives (e.g., "1", "2")
MIN_NUMERIC_ID_LENGTH = 2

# Maximum depth for JSON traversal
MAX_JSON_DEPTH = 5

# Static sentinel values that should never be treated as dynamic correlations.
# Covers JSON null/true/false, Python str(None), empty strings, and common
# default integers. All comparisons should use .lower() before checking.
SKIP_VALUES: frozenset = frozenset({
    "null", "true", "false", "", "none", "undefined",
    "0", "-1",
})


# === OAuth Configuration ===

# OAuth-related parameter names (for flagging, not extraction in Phase 1)
OAUTH_PARAMS: Set[str] = {
    "code", "state", "nonce", "id_token", "access_token", "refresh_token",
    "code_challenge", "code_verifier", "redirect_uri", "client_id",
}

# OAuth token field names in JSON responses (for extraction)
# These are extracted from authentication endpoint responses
OAUTH_TOKEN_FIELDS: Set[str] = {
    "cdssotoken", "cdssoToken",  # Cross-domain SSO token (generic)
    "ssotoken", "ssoToken",      # Generic SSO token
    "tokenid", "tokenId",        # ForgeRock/OpenAM token ID
    "access_token", "accessToken",
    "id_token", "idToken",
    "refresh_token", "refreshToken",
}

# Generic nonce cookie detection patterns (case-insensitive substrings)
# Used to detect nonce values in Set-Cookie headers for OAuth/SSO flows
# These are generic patterns, not company-specific
NONCE_COOKIE_KEYWORDS: tuple = (
    "nonce",      # Generic nonce cookie
    "csrftoken",  # CSRF tokens that may be used as nonce
)

# OAuth token query parameters to parameterize in URLs
# These tokens appear in URLs and need to be substituted with JMeter variables
OAUTH_TOKEN_URL_PARAMS: Set[str] = {
    "cdssotoken",
}


# === EntraID / Microsoft Entra ID Configuration ===

# Fields found inside $Config JavaScript objects on login.microsoftonline.com pages.
# These are embedded in HTML responses as inline JS (e.g. $Config={...}).
ENTRA_CONFIG_FIELDS: Set[str] = {
    "sFT", "sCtx", "canary", "sessionId",
    "correlationId", "hpgact", "hpgid",
    "sFTName", "sErrTxt", "sessionIdentifier",
}

# Fields from the EntraID GetCredentialType API response.
# FederationRedirectUrl may contain embedded URL params (e.g. estsrequest).
ENTRA_CREDENTIAL_TYPE_FIELDS: Set[str] = {
    "FederationRedirectUrl", "ThrottleStatus",
    "IfExistsResult", "IsSignupDisallowed",
}

# WS-Federation hidden form field names in HTML responses.
# These appear in form_post responses during federated authentication.
WSFED_FORM_FIELDS: Set[str] = {
    "wresult", "wctx", "wa", "wtrealm",
}

# OpenAM / ForgeRock token fields in JSON responses.
# These are returned by OpenAM authentication endpoints (e.g. /json/authenticate).
OPENAM_TOKEN_FIELDS: Set[str] = {
    "authId", "nonce", "cdssoToken",
}


# === Request-Side OAuth Detection (Sprint A) ===

# OAuth parameter names to detect in request URLs.
# Used for request-side extraction when response sources are empty.
# Reference: https://auth0.com/docs/get-started/authentication-and-authorization-flow/authorization-code-flow-with-pkce
OAUTH_URL_PARAMS: Set[str] = {
    # Standard OAuth 2.0 / OpenID Connect
    "client_id", "redirect_uri", "response_type", "scope", "state",
    "response_mode", "nonce",
    # PKCE (RFC 7636)
    "code_challenge", "code_challenge_method",
    # Tokens that may appear in URL query strings
    "code", "id_token", "access_token",
    # SSO tokens in URLs (cross-domain SSO flows)
    "cdssotoken", "ssotoken",
}

# Parameters whose values may contain nested URLs with more OAuth params.
# These are recursively URL-decoded and re-parsed to extract embedded params.
OAUTH_NESTED_URL_PARAMS: Set[str] = {
    "goto", "redirect_uri", "return_url", "returnurl",
    "redirect", "callback", "continue",
}

# Mapping from URL param name (lowercase) to value_type classification
OAUTH_PARAM_VALUE_TYPES: Dict[str, str] = {
    "client_id": "oauth_client_id",
    "redirect_uri": "oauth_redirect_uri",
    "response_type": "oauth_response_type",
    "scope": "oauth_scope",
    "state": "oauth_state",
    "response_mode": "oauth_response_mode",
    "nonce": "oauth_nonce",
    "code_challenge": "pkce_code_challenge",
    "code_challenge_method": "pkce_code_challenge_method",
    "code": "oauth_code",
    "id_token": "oauth_token",
    "access_token": "oauth_token",
    "cdssotoken": "sso_token",
    "ssotoken": "sso_token",
}

# PKCE-specific parameter names (subset of OAUTH_URL_PARAMS)
PKCE_PARAMS: Set[str] = {"code_challenge", "code_challenge_method", "code_verifier"}

# === SAML Configuration ===

# SAML parameter names in redirect URLs (SSO flows)
SAML_PARAMS: Set[str] = {"samlrequest", "samlresponse", "samlart"}

# Mapping from SAML param name (lowercase) to value_type classification
SAML_PARAM_VALUE_TYPES: Dict[str, str] = {
    "samlrequest": "saml_request",
    "samlresponse": "saml_response",
    "samlart": "saml_artifact",
}

# Request headers that carry dynamic OAuth/SSO values (nonces, CSRF tokens).
# These are custom headers where the VALUE is a token/nonce needing correlation.
# Standard headers like Authorization (Bearer) are handled separately by config_elements.
OAUTH_INTEREST_HEADERS: Set[str] = {
    "x-cdsso-nonce",
    "x-csrf-token",
    "x-xsrf-token",
}

# Mapping from interest header name (lowercase) to value_type classification
OAUTH_INTEREST_HEADER_VALUE_TYPES: Dict[str, str] = {
    "x-cdsso-nonce": "sso_nonce",
    "x-csrf-token": "csrf_token",
    "x-xsrf-token": "csrf_token",
}

# OAuth parameters to detect in form-urlencoded POST request bodies (token endpoints)
OAUTH_BODY_PARAMS: Set[str] = {
    "grant_type", "code", "code_verifier", "subject_token",
    "client_id", "redirect_uri", "scope", "refresh_token",
    "client_secret", "assertion",
}

# Known OAuth grant_type values for flow classification
OAUTH_GRANT_TYPES: Dict[str, str] = {
    "authorization_code": "pkce_or_auth_code",
    "urn:ietf:params:oauth:grant-type:token-exchange": "token_exchange",
    "refresh_token": "refresh_token",
    "client_credentials": "client_credentials",
}

# Mapping from POST body param name (lowercase) to value_type classification
OAUTH_BODY_PARAM_VALUE_TYPES: Dict[str, str] = {
    "code": "oauth_code",
    "code_verifier": "pkce_code_verifier",
    "subject_token": "oauth_subject_token",
    "client_id": "oauth_client_id",
    "redirect_uri": "oauth_redirect_uri",
    "scope": "oauth_scope",
    "refresh_token": "oauth_refresh_token",
    "client_secret": "oauth_client_secret",
    "assertion": "oauth_assertion",
}

# === Static API Key Header Detection ===
# Pattern for header names that typically carry static API keys or
# subscription keys.  Matched case-insensitively against request headers.
# Catches any header ending in "-key" (e.g. x-api-key, x-functions-key,
# *-subscription-key, my-service-key, etc.)
import re as _re
API_KEY_HEADER_RE = _re.compile(r"-key$", _re.IGNORECASE)

# services/jmx/oauth2.py
"""
JMeter OAuth 2.0 / OpenID Connect JMX element builders.

Creates JMX elements for OAuth token extraction and refresh flows.
Wraps the generic extractors in post_processor.py with OAuth-aware defaults.
"""
from typing import Dict, Optional, Tuple
import xml.etree.ElementTree as ET

from .post_processor import create_json_extractor


# Default JSONPath expressions for common OAuth token types.
# Callers can override by passing an explicit json_path argument.
TOKEN_TYPE_JSONPATHS: Dict[str, str] = {
    "access_token": "$.access_token",
    "id_token": "$.id_token",
    "refresh_token": "$.refresh_token",
    "token_type": "$.token_type",
    "expires_in": "$.expires_in",
    "scope": "$.scope",
    # SSO / ForgeRock / OpenAM tokens
    "cdssotoken": "$.tokenId",
    "sso_token": "$.tokenId",
    "tokenid": "$.tokenId",
}


def create_oauth_token_extractor(
    variable_name: str,
    json_path: Optional[str] = None,
    token_type: str = "access_token",
    default_value: str = "NOT_FOUND",
) -> ET.Element:
    """
    Creates a JSON Extractor element for extracting an OAuth token.

    Wraps create_json_extractor() with OAuth-specific defaults:
    - Derives json_path from token_type when not explicitly provided.
    - Sets a descriptive testname (e.g. "Extract OAuth access_token").
    - Uses NOT_FOUND as default so failures are immediately visible.

    Args:
        variable_name: JMeter variable to store the extracted value.
        json_path: JSONPath expression. If None, inferred from token_type
                   via TOKEN_TYPE_JSONPATHS (falls back to $.{token_type}).
        token_type: One of "access_token", "id_token", "refresh_token",
                    "cdssotoken", "sso_token", "tokenid", etc.
        default_value: Value when extraction fails (default "NOT_FOUND").

    Returns:
        ET.Element: JSONPostProcessor element ready for JMX hashTree insertion.
    """
    if json_path is None:
        json_path = TOKEN_TYPE_JSONPATHS.get(
            token_type, f"$.{token_type}"
        )

    testname = f"Extract OAuth {token_type}"

    return create_json_extractor(
        variable_name=variable_name,
        json_path=json_path,
        match_no="1",
        default_value=default_value,
        testname=testname,
    )

def create_oauth_refresh_flow(
    refresh_endpoint_url: str,
    client_id: str,
    current_token_var: str,
    refresh_token_var: str,
    output_var: str,
) -> Tuple[ET.Element, ET.Element]:
    """
    Creates a POST sampler + hashTree for calling an OAuth refresh-token endpoint.

    Generates the JMX structure for a standard OAuth 2.0 refresh token grant:
      POST {refresh_endpoint_url}
      Content-Type: application/x-www-form-urlencoded
      Body: grant_type=refresh_token&client_id={client_id}&refresh_token=${refresh_token_var}

    The hashTree includes:
      - Header Manager (Content-Type)
      - JSON Extractor for new access_token → output_var
      - JSON Extractor for rotated refresh_token → refresh_token_var

    Args:
        refresh_endpoint_url: Full URL of the token endpoint (e.g.
            "https://auth.example.com/oauth/token" or "${token_endpoint}").
        client_id: OAuth client ID — literal value or JMeter variable
            reference (e.g. "${client_id}").
        current_token_var: JMeter variable holding the current access token.
            Not sent in the request but used in the testname for traceability.
        refresh_token_var: JMeter variable name holding the refresh token.
            The new refresh token (if rotated by the server) is stored back
            into this same variable.
        output_var: JMeter variable name to store the newly issued access token.

    Returns:
        Tuple of (HTTPSamplerProxy element, hashTree element).
        Caller inserts both into the parent hashTree sequentially.

    Note:
        This is a structural implementation. It has not yet been validated
        against a real refresh-token capture. Refine after Sprint D / when
        a refresh-token example is available.
    """
    import urllib.parse

    parsed = urllib.parse.urlparse(refresh_endpoint_url)
    domain = parsed.netloc or refresh_endpoint_url
    protocol = parsed.scheme or "https"
    path = parsed.path or "/oauth/token"

    post_body = (
        f"grant_type=refresh_token"
        f"&client_id={client_id}"
        f"&refresh_token=${{{refresh_token_var}}}"
    )

    # --- HTTP Sampler (POST) ---
    sampler = ET.Element("HTTPSamplerProxy", attrib={
        "guiclass": "HttpTestSampleGui",
        "testclass": "HTTPSamplerProxy",
        "testname": f"OAuth Refresh Token ({current_token_var})",
        "enabled": "true",
    })
    ET.SubElement(sampler, "stringProp", attrib={
        "name": "HTTPSampler.domain"
    }).text = domain
    ET.SubElement(sampler, "stringProp", attrib={
        "name": "HTTPSampler.protocol"
    }).text = protocol
    ET.SubElement(sampler, "stringProp", attrib={
        "name": "HTTPSampler.path"
    }).text = path
    ET.SubElement(sampler, "stringProp", attrib={
        "name": "HTTPSampler.method"
    }).text = "POST"
    ET.SubElement(sampler, "boolProp", attrib={
        "name": "HTTPSampler.postBodyRaw"
    }).text = "true"

    args_prop = ET.SubElement(sampler, "elementProp", attrib={
        "name": "HTTPsampler.Arguments",
        "elementType": "Arguments",
    })
    coll_prop = ET.SubElement(args_prop, "collectionProp", attrib={
        "name": "Arguments.arguments"
    })
    arg_el = ET.SubElement(coll_prop, "elementProp", attrib={
        "name": "", "elementType": "HTTPArgument"
    })
    ET.SubElement(arg_el, "boolProp", attrib={
        "name": "HTTPArgument.always_encode"
    }).text = "false"
    ET.SubElement(arg_el, "stringProp", attrib={
        "name": "Argument.value"
    }).text = post_body
    ET.SubElement(arg_el, "stringProp", attrib={
        "name": "Argument.metadata"
    }).text = "="
    ET.SubElement(arg_el, "boolProp", attrib={
        "name": "HTTPArgument.use_equals"
    }).text = "true"

    # --- hashTree (Header Manager + Extractors) ---
    hash_tree = ET.Element("hashTree")

    # Header Manager — Content-Type
    hm = ET.Element("HeaderManager", attrib={
        "guiclass": "HeaderPanel",
        "testclass": "HeaderManager",
        "testname": "HTTP Header Manager",
        "enabled": "true",
    })
    hm_coll = ET.SubElement(hm, "collectionProp", attrib={
        "name": "HeaderManager.headers"
    })
    hdr_el = ET.SubElement(hm_coll, "elementProp", attrib={
        "name": "", "elementType": "Header"
    })
    ET.SubElement(hdr_el, "stringProp", attrib={
        "name": "Header.name"
    }).text = "Content-Type"
    ET.SubElement(hdr_el, "stringProp", attrib={
        "name": "Header.value"
    }).text = "application/x-www-form-urlencoded"

    hash_tree.append(hm)
    hash_tree.append(ET.Element("hashTree"))

    # JSON Extractor — new access_token
    access_ext = create_json_extractor(
        variable_name=output_var,
        json_path="$.access_token",
        default_value="NOT_FOUND",
        testname=f"Extract refreshed access_token → {output_var}",
    )
    hash_tree.append(access_ext)
    hash_tree.append(ET.Element("hashTree"))

    # JSON Extractor — rotated refresh_token (servers may issue a new one)
    refresh_ext = create_json_extractor(
        variable_name=refresh_token_var,
        json_path="$.refresh_token",
        default_value=f"${{{refresh_token_var}}}",
        testname=f"Extract rotated refresh_token → {refresh_token_var}",
    )
    hash_tree.append(refresh_ext)
    hash_tree.append(ET.Element("hashTree"))

    return sampler, hash_tree
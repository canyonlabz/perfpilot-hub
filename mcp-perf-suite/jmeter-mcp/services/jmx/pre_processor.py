# services/jmx/pre_processor.py
"""
JMeter Pre-Processor Elements

This module contains functions to create JMeter pre-processor elements,
primarily for dynamic value generation before HTTP requests:
- JSR223 PreProcessor (Groovy) - Execute custom scripts
- Timestamp generation for SignalR/cache-busting parameters
- PKCE (Proof Key for Code Exchange) code_verifier/code_challenge generation for OAuth 2.0
- Cookie addition for cross-domain cookie scenarios
- MSAL (Microsoft Authentication Library) oauth_state generation for OAuth 2.0
- EntraID WS-Federation cookie injection for EntraID SSO

Pre-processors are placed inside an HTTP Sampler's hashTree and execute
before the sampler runs, allowing dynamic value generation.
"""
import xml.etree.ElementTree as ET


def create_jsr223_preprocessor(
    script: str,
    language: str = "groovy",
    testname: str = "JSR223 PreProcessor",
    cache_key: str = "true",
    parameters: str = ""
) -> ET.Element:
    """
    Creates a JSR223 PreProcessor element.
    
    JSR223 PreProcessors execute custom scripts before an HTTP sampler runs.
    Commonly used for dynamic value generation, request modification, or
    complex logic that can't be achieved with built-in JMeter functions.
    
    Args:
        script: The script code to execute (Groovy, JavaScript, etc.)
        language: Script language (default: "groovy" - recommended for performance)
        testname: Display name in JMeter
        cache_key: Whether to cache the compiled script ("true" for better performance)
        parameters: Optional parameters to pass to the script
    
    Returns:
        ET.Element: The JSR223PreProcessor XML element
    
    Example JMX output:
        <JSR223PreProcessor guiclass="TestBeanGUI" 
                           testclass="JSR223PreProcessor" 
                           testname="Generate Timestamp" enabled="true">
          <stringProp name="scriptLanguage">groovy</stringProp>
          <stringProp name="parameters"></stringProp>
          <stringProp name="filename"></stringProp>
          <stringProp name="cacheKey">true</stringProp>
          <stringProp name="script">vars.put("timestamp", System.currentTimeMillis().toString())</stringProp>
        </JSR223PreProcessor>
    """
    preprocessor = ET.Element("JSR223PreProcessor", attrib={
        "guiclass": "TestBeanGUI",
        "testclass": "JSR223PreProcessor",
        "testname": testname,
        "enabled": "true"
    })
    
    # Script language (groovy recommended for performance)
    ET.SubElement(preprocessor, "stringProp", attrib={
        "name": "scriptLanguage"
    }).text = language
    
    # Parameters (passed to script as 'Parameters' variable)
    ET.SubElement(preprocessor, "stringProp", attrib={
        "name": "parameters"
    }).text = parameters
    
    # External script filename (empty for inline script)
    ET.SubElement(preprocessor, "stringProp", attrib={
        "name": "filename"
    }).text = ""
    
    # Cache key for compiled script (improves performance)
    ET.SubElement(preprocessor, "stringProp", attrib={
        "name": "cacheKey"
    }).text = cache_key
    
    # The actual script code
    ET.SubElement(preprocessor, "stringProp", attrib={
        "name": "script"
    }).text = script
    
    return preprocessor


def create_timestamp_preprocessor(
    variable_name: str = "timestamp",
    testname: str = None
) -> ET.Element:
    """
    Creates a JSR223 PreProcessor that generates a timestamp.
    
    Generates a millisecond timestamp and stores it in a JMeter variable.
    Useful for SignalR cache-busting parameters, request IDs, etc.
    
    Args:
        variable_name: Name of the JMeter variable to store the timestamp
        testname: Display name in JMeter (defaults to "Generate {variable_name}")
    
    Returns:
        ET.Element: The JSR223PreProcessor XML element
    
    Example usage in JMeter:
        The generated timestamp can be used as ${timestamp} in subsequent requests
    """
    if testname is None:
        testname = f"Generate {variable_name}"
    
    script = f'vars.put("{variable_name}", System.currentTimeMillis().toString())'
    
    return create_jsr223_preprocessor(
        script=script,
        testname=testname
    )


def create_multiple_timestamps_preprocessor(
    variable_names: list,
    testname: str = "Generate Timestamps"
) -> ET.Element:
    """
    Creates a JSR223 PreProcessor that generates multiple sequential timestamps.
    
    Each timestamp is incremented by 1ms to ensure uniqueness, matching
    the pattern seen in SignalR negotiate/start requests.
    
    Args:
        variable_names: List of JMeter variable names to store timestamps
        testname: Display name in JMeter
    
    Returns:
        ET.Element: The JSR223PreProcessor XML element
    
    Example:
        create_multiple_timestamps_preprocessor(["ts1", "ts2", "ts3"])
        
        Results in:
        - ts1 = 1764863629972
        - ts2 = 1764863629973
        - ts3 = 1764863629974
    """
    script_lines = [
        "def baseTime = System.currentTimeMillis()"
    ]
    
    for i, var_name in enumerate(variable_names):
        script_lines.append(f'vars.put("{var_name}", (baseTime + {i}).toString())')
    
    script = "\n".join(script_lines)
    
    return create_jsr223_preprocessor(
        script=script,
        testname=testname
    )


def create_uuid_preprocessor(
    variable_name: str = "uuid",
    testname: str = None
) -> ET.Element:
    """
    Creates a JSR223 PreProcessor that generates a random UUID.
    
    Useful for generating unique request IDs, correlation IDs, etc.
    
    Args:
        variable_name: Name of the JMeter variable to store the UUID
        testname: Display name in JMeter (defaults to "Generate {variable_name}")
    
    Returns:
        ET.Element: The JSR223PreProcessor XML element
    """
    if testname is None:
        testname = f"Generate {variable_name}"
    
    script = f'vars.put("{variable_name}", java.util.UUID.randomUUID().toString())'
    
    return create_jsr223_preprocessor(
        script=script,
        testname=testname
    )


def create_pkce_preprocessor(
    code_verifier_var: str = "code_verifier",
    code_challenge_var: str = "code_challenge",
    testname: str = "Generate PKCE Values"
) -> ET.Element:
    """
    Creates a JSR223 PreProcessor that generates PKCE (Proof Key for Code Exchange) values.
    
    PKCE is used in OAuth 2.0 authorization code flow to prevent authorization
    code interception attacks. This generates:
    - code_verifier: Random 43-128 character string
    - code_challenge: Base64URL encoded SHA256 hash of code_verifier
    
    Args:
        code_verifier_var: Variable name for the code verifier
        code_challenge_var: Variable name for the code challenge
        testname: Display name in JMeter
    
    Returns:
        ET.Element: The JSR223PreProcessor XML element
    
    Note:
        The code_challenge_method should be "S256" when using this preprocessor.
    """
    # Groovy script for PKCE generation
    script = f'''import java.security.SecureRandom
import java.security.MessageDigest
import java.util.Base64

// Generate code_verifier (43-128 characters, URL-safe)
def secureRandom = new SecureRandom()
def codeVerifierBytes = new byte[32]
secureRandom.nextBytes(codeVerifierBytes)
def codeVerifier = Base64.getUrlEncoder().withoutPadding().encodeToString(codeVerifierBytes)

// Generate code_challenge (SHA256 hash, Base64URL encoded)
def digest = MessageDigest.getInstance("SHA-256")
def hash = digest.digest(codeVerifier.getBytes("UTF-8"))
def codeChallenge = Base64.getUrlEncoder().withoutPadding().encodeToString(hash)

// Store in JMeter variables
vars.put("{code_verifier_var}", codeVerifier)
vars.put("{code_challenge_var}", codeChallenge)

log.info("PKCE generated - code_verifier: " + codeVerifier.substring(0, 10) + "...")
'''
    
    return create_jsr223_preprocessor(
        script=script,
        testname=testname
    )


def create_cookie_preprocessor(
    cookie_name: str,
    cookie_value_var: str,
    domain: str,
    testname: str = None
) -> ET.Element:
    """
    Creates a JSR223 PreProcessor that adds a cookie to the Cookie Manager.
    
    Useful for cross-domain cookie scenarios where cookies need to be
    manually added to requests (e.g., SSO flows, federated authentication).
    
    Args:
        cookie_name: Name of the cookie
        cookie_value_var: JMeter variable containing the cookie value
        domain: Domain for the cookie
        testname: Display name in JMeter
    
    Returns:
        ET.Element: The JSR223PreProcessor XML element
    
    Example:
        create_cookie_preprocessor("session_id", "extracted_session_id", "api.example.com")
        
        This will add a cookie from the variable ${extracted_session_id} to requests.
    """
    if testname is None:
        testname = f"Add Cookie: {cookie_name}"
    
    script = f'''import org.apache.jmeter.protocol.http.control.Cookie

def cookieManager = sampler.getCookieManager()
def cookieValue = vars.get("{cookie_value_var}")

if (cookieValue != null && !cookieValue.isEmpty()) {{
    def cookie = new Cookie("{cookie_name}", cookieValue, "{domain}", "/", false, Long.MAX_VALUE)
    cookieManager.add(cookie)
    log.info("Added cookie {cookie_name} for domain {domain}")
}} else {{
    log.warn("Cookie value variable {cookie_value_var} is null or empty")
}}
'''
    
    return create_jsr223_preprocessor(
        script=script,
        testname=testname
    )


def create_entra_state_preprocessor(
    state_var: str = "oauth_state",
    testname: str = "Generate MSAL oauth_state"
) -> ET.Element:
    """
    Creates a JSR223 PreProcessor that generates an MSAL-format oauth_state.

    Microsoft Authentication Library (MSAL) uses a base64url-encoded JSON
    structure for the OAuth state parameter:
        {id: "<UUID>", meta: {interactionType: "redirect"}}

    Args:
        state_var: Variable name for the encoded state value
        testname: Display name in JMeter

    Returns:
        ET.Element: The JSR223PreProcessor XML element
    """
    script = f'''import java.util.Base64
import groovy.json.JsonOutput

def stateId = UUID.randomUUID().toString()
def stateJson = JsonOutput.toJson([id: stateId, meta: [interactionType: "redirect"]])
def encoded = Base64.getUrlEncoder().withoutPadding().encodeToString(stateJson.getBytes("UTF-8"))
vars.put("{state_var}", encoded)

log.info("MSAL state generated - id: " + stateId)
'''

    return create_jsr223_preprocessor(
        script=script,
        testname=testname
    )


def create_entra_wsfed_cookie_preprocessor(
    flow_token_var: str = "flowToken_1",
    domain: str = "login.microsoftonline.com",
    testname: str = "Add EntraID WS-Fed Cookies"
) -> ET.Element:
    """
    Creates a BeanShell PreProcessor that injects EntraID WS-Federation cookies.

    During the WS-Fed form submission step, EntraID expects two cookies:
    - ESTSWCTXFLOWTOKEN: Contains the flow token from the previous $Config
    - AADSSO: Static value "NA|NoExtension" signaling no SSO extension

    Uses BeanShell (not Groovy) because JMeter's CookieManager API is more
    reliably accessible from BeanShell PreProcessors in older JMeter versions.

    Args:
        flow_token_var: JMeter variable containing the flow token value
        domain: Cookie domain (default: login.microsoftonline.com)
        testname: Display name in JMeter

    Returns:
        ET.Element: The BeanShellPreProcessor XML element
    """
    script = f'''import org.apache.jmeter.protocol.http.control.CookieManager;
import org.apache.jmeter.protocol.http.control.Cookie;

CookieManager manager = sampler.getCookieManager();
Cookie cookie1 = new Cookie("ESTSWCTXFLOWTOKEN","${{{flow_token_var}}}","{domain}","/",false,0);
Cookie cookie2 = new Cookie("AADSSO","NA|NoExtension","{domain}","/",false,0);
manager.add(cookie1);
manager.add(cookie2);
'''

    preprocessor = ET.Element("BeanShellPreProcessor", attrib={
        "guiclass": "TestBeanGUI",
        "testclass": "BeanShellPreProcessor",
        "testname": testname,
        "enabled": "true"
    })

    ET.SubElement(preprocessor, "stringProp", attrib={
        "name": "filename"
    }).text = ""

    ET.SubElement(preprocessor, "stringProp", attrib={
        "name": "parameters"
    }).text = ""

    ET.SubElement(preprocessor, "boolProp", attrib={
        "name": "resetInterpreter"
    }).text = "false"

    ET.SubElement(preprocessor, "stringProp", attrib={
        "name": "script"
    }).text = script

    return preprocessor


# === Helper function to append preprocessor to sampler hashTree ===
def append_preprocessor(sampler_hash_tree: ET.Element, preprocessor: ET.Element) -> None:
    """
    Appends a preprocessor element to a sampler's hashTree.
    
    Pre-processors should be added BEFORE extractors (post-processors) in the hashTree.
    In JMeter's JMX structure, each element must be followed by its own empty hashTree.
    
    Args:
        sampler_hash_tree: The hashTree element belonging to the HTTP Sampler
        preprocessor: The preprocessor element (JSR223, etc.)
    
    Example structure after appending:
        <hashTree>  <!-- sampler_hash_tree -->
          <JSR223PreProcessor>...</JSR223PreProcessor>
          <hashTree/>  <!-- empty hashTree for preprocessor -->
          <JSONPostProcessor>...</JSONPostProcessor>  <!-- extractors come after -->
          <hashTree/>
        </hashTree>
    """
    # Insert at the beginning of the hashTree (before extractors)
    sampler_hash_tree.insert(0, ET.Element("hashTree"))
    sampler_hash_tree.insert(0, preprocessor)

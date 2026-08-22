# services/jmx/post_processor.py
"""
JMeter Post-Processor Elements

This module contains functions to create JMeter post-processor elements,
primarily extractors for correlation support:
- JSON Extractor (JSONPostProcessor) - Extract values from JSON responses
- Regular Expression Extractor (RegexExtractor) - Extract values using regex

These extractors are placed inside an HTTP Sampler's hashTree to capture
dynamic values from responses for use in subsequent requests.
"""
import xml.etree.ElementTree as ET


def create_json_extractor(
    variable_name: str,
    json_path: str,
    match_no: str = "1",
    default_value: str = "NOT_FOUND",
    testname: str = None
) -> ET.Element:
    """
    Creates a JSON Extractor (JSONPostProcessor) element.
    
    Used to extract values from JSON responses using JSONPath expressions.
    
    Args:
        variable_name: Name of the JMeter variable to store the extracted value
        json_path: JSONPath expression (e.g., "$.items[0].id", "$.data.token")
        match_no: Which match to use ("1" for first, "0" for random, "-1" for all)
        default_value: Value to use if extraction fails
        testname: Display name in JMeter (defaults to "Extract {variable_name}")
    
    Returns:
        ET.Element: The JSONPostProcessor XML element
    
    Example JMX output:
        <JSONPostProcessor guiclass="JSONPostProcessorGui" 
                          testclass="JSONPostProcessor" 
                          testname="Extract product_id" enabled="true">
          <stringProp name="JSONPostProcessor.referenceNames">product_id</stringProp>
          <stringProp name="JSONPostProcessor.jsonPathExprs">$.Items[5].id</stringProp>
          <stringProp name="JSONPostProcessor.match_numbers">1</stringProp>
          <stringProp name="JSONPostProcessor.defaultValues">NOT_FOUND</stringProp>
        </JSONPostProcessor>
    """
    if testname is None:
        testname = f"Extract {variable_name}"
    
    extractor = ET.Element("JSONPostProcessor", attrib={
        "guiclass": "JSONPostProcessorGui",
        "testclass": "JSONPostProcessor",
        "testname": testname,
        "enabled": "true"
    })
    
    # Variable name to store extracted value
    ET.SubElement(extractor, "stringProp", attrib={
        "name": "JSONPostProcessor.referenceNames"
    }).text = variable_name
    
    # JSONPath expression
    ET.SubElement(extractor, "stringProp", attrib={
        "name": "JSONPostProcessor.jsonPathExprs"
    }).text = json_path
    
    # Match number (1=first, 0=random, -1=all)
    ET.SubElement(extractor, "stringProp", attrib={
        "name": "JSONPostProcessor.match_numbers"
    }).text = str(match_no)
    
    # Default value if not found
    ET.SubElement(extractor, "stringProp", attrib={
        "name": "JSONPostProcessor.defaultValues"
    }).text = default_value
    
    return extractor


def create_regex_extractor(
    variable_name: str,
    regex: str,
    template: str = "$1$",
    match_no: str = "1",
    default_value: str = "NOT_FOUND",
    field_to_check: str = "body",
    testname: str = None
) -> ET.Element:
    """
    Creates a Regular Expression Extractor element.
    
    Used to extract values from responses using regular expressions.
    Commonly used for extracting from headers, redirect URLs, and cookies.
    
    Args:
        variable_name: Name of the JMeter variable to store the extracted value
        regex: Regular expression pattern with capturing group(s)
        template: Template to build result from groups (e.g., "$1$" for first group)
        match_no: Which match to use ("1" for first, "0" for random, "-1" for all)
        default_value: Value to use if extraction fails
        field_to_check: Where to search for the pattern:
            - "body" (default): Response body
            - "headers": Response headers (including Set-Cookie)
            - "url": Response URL (useful for redirects)
            - "code": Response code
            - "message": Response message
        testname: Display name in JMeter (defaults to "Extract {variable_name}")
    
    Returns:
        ET.Element: The RegexExtractor XML element
    
    Example JMX output (extracting from header):
        <RegexExtractor guiclass="RegexExtractorGui" 
                       testclass="RegexExtractor" 
                       testname="Extract session_token" enabled="true">
          <stringProp name="RegexExtractor.useHeaders">true</stringProp>
          <stringProp name="RegexExtractor.refname">session_token</stringProp>
          <stringProp name="RegexExtractor.regex">X-Session-Token: (.+)</stringProp>
          <stringProp name="RegexExtractor.template">$1$</stringProp>
          <stringProp name="RegexExtractor.default">NOT_FOUND</stringProp>
          <stringProp name="RegexExtractor.match_number">1</stringProp>
        </RegexExtractor>
    """
    if testname is None:
        testname = f"Extract {variable_name}"
    
    extractor = ET.Element("RegexExtractor", attrib={
        "guiclass": "RegexExtractorGui",
        "testclass": "RegexExtractor",
        "testname": testname,
        "enabled": "true"
    })
    
    # Field to check - maps our friendly names to JMeter's values
    field_mapping = {
        "body": "false",      # Search in response body
        "headers": "true",    # Search in response headers
        "url": "URL",         # Search in response URL (redirect)
        "code": "code",       # Search in response code
        "message": "message"  # Search in response message
    }
    use_headers_value = field_mapping.get(field_to_check.lower(), "false")
    
    ET.SubElement(extractor, "stringProp", attrib={
        "name": "RegexExtractor.useHeaders"
    }).text = use_headers_value
    
    # Variable name to store extracted value
    ET.SubElement(extractor, "stringProp", attrib={
        "name": "RegexExtractor.refname"
    }).text = variable_name
    
    # Regular expression pattern
    ET.SubElement(extractor, "stringProp", attrib={
        "name": "RegexExtractor.regex"
    }).text = regex
    
    # Template for result (e.g., "$1$" uses first capture group)
    ET.SubElement(extractor, "stringProp", attrib={
        "name": "RegexExtractor.template"
    }).text = template
    
    # Default value if not found
    ET.SubElement(extractor, "stringProp", attrib={
        "name": "RegexExtractor.default"
    }).text = default_value
    
    # Match number (1=first, 0=random, -1=all)
    ET.SubElement(extractor, "stringProp", attrib={
        "name": "RegexExtractor.match_number"
    }).text = str(match_no)
    
    return extractor


def create_boundary_extractor(
    variable_name: str,
    left_boundary: str,
    right_boundary: str,
    match_no: str = "1",
    default_value: str = "NOT_FOUND",
    field_to_check: str = "body",
    testname: str = None
) -> ET.Element:
    """
    Creates a Boundary Extractor element.
    
    Extracts text between two boundaries. Simpler than regex for basic cases.
    Useful when you know the exact text surrounding the value to extract.
    
    Args:
        variable_name: Name of the JMeter variable to store the extracted value
        left_boundary: Text that appears before the value to extract
        right_boundary: Text that appears after the value to extract
        match_no: Which match to use ("1" for first, "0" for random, "-1" for all)
        default_value: Value to use if extraction fails
        field_to_check: Where to search ("body", "headers", "url", "code", "message")
        testname: Display name in JMeter (defaults to "Extract {variable_name}")
    
    Returns:
        ET.Element: The BoundaryExtractor XML element
    
    Example: Extract value between "token\":" and "," in JSON
        left_boundary: 'token":"'
        right_boundary: '"'
        Result: Extracts the token value
    """
    if testname is None:
        testname = f"Extract {variable_name}"
    
    extractor = ET.Element("BoundaryExtractor", attrib={
        "guiclass": "BoundaryExtractorGui",
        "testclass": "BoundaryExtractor",
        "testname": testname,
        "enabled": "true"
    })
    
    # Field to check mapping
    field_mapping = {
        "body": "false",
        "headers": "true",
        "url": "URL",
        "code": "code",
        "message": "message"
    }
    use_headers_value = field_mapping.get(field_to_check.lower(), "false")
    
    ET.SubElement(extractor, "stringProp", attrib={
        "name": "BoundaryExtractor.useHeaders"
    }).text = use_headers_value
    
    # Variable name
    ET.SubElement(extractor, "stringProp", attrib={
        "name": "BoundaryExtractor.refname"
    }).text = variable_name
    
    # Left boundary
    ET.SubElement(extractor, "stringProp", attrib={
        "name": "BoundaryExtractor.lboundary"
    }).text = left_boundary
    
    # Right boundary
    ET.SubElement(extractor, "stringProp", attrib={
        "name": "BoundaryExtractor.rboundary"
    }).text = right_boundary
    
    # Default value
    ET.SubElement(extractor, "stringProp", attrib={
        "name": "BoundaryExtractor.default"
    }).text = default_value
    
    # Match number
    ET.SubElement(extractor, "stringProp", attrib={
        "name": "BoundaryExtractor.match_number"
    }).text = str(match_no)
    
    return extractor


# === Helper function to append extractor to sampler hashTree ===
def append_extractor(sampler_hash_tree: ET.Element, extractor: ET.Element) -> None:
    """
    Appends an extractor element to a sampler's hashTree.
    
    In JMeter's JMX structure, extractors must be placed inside the 
    HTTP Sampler's hashTree, followed by their own empty hashTree.
    
    Args:
        sampler_hash_tree: The hashTree element belonging to the HTTP Sampler
        extractor: The extractor element (JSON, Regex, or Boundary)
    
    Example structure after appending:
        <hashTree>  <!-- sampler_hash_tree -->
          <JSONPostProcessor>...</JSONPostProcessor>
          <hashTree/>  <!-- empty hashTree for extractor -->
        </hashTree>
    """
    sampler_hash_tree.append(extractor)
    sampler_hash_tree.append(ET.Element("hashTree"))


def create_jsr223_postprocessor(
    script: str,
    language: str = "groovy",
    testname: str = "JSR223 PostProcessor",
    cache_key: str = "true",
    parameters: str = ""
) -> ET.Element:
    """
    Creates a JSR223 PostProcessor element.

    JSR223 PostProcessors execute custom scripts after an HTTP sampler runs.
    Commonly used for parsing responses, computing flags, custom extractions,
    or any post-response logic beyond built-in extractors.

    Args:
        script: The script code to execute (Groovy recommended).
        language: Script language (default: "groovy").
        testname: Display name in JMeter.
        cache_key: Whether to cache the compiled script ("true" recommended).
        parameters: Optional parameters passed to the script.

    Returns:
        ET.Element: The JSR223PostProcessor XML element.
    """
    postprocessor = ET.Element("JSR223PostProcessor", attrib={
        "guiclass": "TestBeanGUI",
        "testclass": "JSR223PostProcessor",
        "testname": testname,
        "enabled": "true"
    })
    ET.SubElement(postprocessor, "stringProp", attrib={
        "name": "scriptLanguage"
    }).text = language
    ET.SubElement(postprocessor, "stringProp", attrib={
        "name": "parameters"
    }).text = parameters
    ET.SubElement(postprocessor, "stringProp", attrib={
        "name": "filename"
    }).text = ""
    ET.SubElement(postprocessor, "stringProp", attrib={
        "name": "cacheKey"
    }).text = cache_key
    ET.SubElement(postprocessor, "stringProp", attrib={
        "name": "script"
    }).text = script

    return postprocessor


def create_jsr223_debug_postprocessor(
    testname: str = "AI Debug PostProcessor",
    cache_key: str = "true",
) -> ET.Element:
    """
    Creates a JSR223 PostProcessor pre-loaded with an AI debug Groovy script.

    When added to an HTTP sampler, this post-processor logs verbose
    request/response details to the JMeter log for any sampler that fails.
    Output is gated by the VERBOSE_LOGGING User Defined Variable (must be
    set to "true" to produce output).

    Intended for AI-assisted script debugging workflows: attach to a failing
    sampler, run a 1-user smoke test, then read the JMeter log to diagnose
    correlation, parameterization, or server-side issues.

    Args:
        testname: Display name in JMeter (default: "AI Debug PostProcessor").
        cache_key: Whether to cache the compiled script ("true" recommended).

    Returns:
        ET.Element: The JSR223PostProcessor XML element with the debug script.
    """
    script = """\
def threadName   = prev.getThreadName()
def verboseMode  = "true".equalsIgnoreCase(vars.get("VERBOSE_LOGGING"))
def samplerName  = prev.getSampleLabel()
def success      = prev.isSuccessful()
def logMsg       = ""

if (verboseMode && !success) {
    logMsg += "[ERROR]:[DEBUG]:[" + threadName + "]: " + samplerName + ": Response Code=${prev.getResponseCode()}\\n"
    logMsg += "[ERROR]:[DEBUG]:[" + threadName + "]: " + samplerName + ": Response Message=${prev.getResponseMessage()}\\n"
    logMsg += "[ERROR]:[DEBUG]:[" + threadName + "]: " + samplerName + ": Request=[" + prev.getSamplerData() + "]\\n"
    logMsg += "[ERROR]:[DEBUG]:[" + threadName + "]: " + samplerName + ": Response=[" + prev.getResponseDataAsString() + "]\\n"
    log.error(logMsg)
}
"""
    return create_jsr223_postprocessor(
        script=script,
        language="groovy",
        testname=testname,
        cache_key=cache_key,
    )

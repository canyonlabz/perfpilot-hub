"""
services/jmx/samplers.py

This module contains functions to create JMeter Sampler components.
For example, it includes functions to generate "HTTP Request", "Flow Control Action" components, etc.
End-users and developers can extend or modify these functions independently
of the core JMeter component utilities.
"""
import xml.etree.ElementTree as ET
import datetime
import os
import re
import urllib.parse
from xml.dom import minidom
from services.jmx.config_elements import (
    create_header_manager
)

_JMETER_VAR_RE = re.compile(r'\$\{')


def _sanitize_label(name: str) -> str:
    """Strip the '$' from JMeter variable references in sampler names.

    Converts '${var}' to '{var}' so JMeter treats the name as a literal
    string at runtime, keeping aggregate report grouping intact across
    multiple virtual users.
    """
    return _JMETER_VAR_RE.sub('{', name)

# === HTTP Sampler for GET Requests ===
def create_http_sampler_get(entry, hostname_var_map=None, exclude_http2_pseudo_headers=True, testname_prefix=None):
    """
    Creates an HTTP Sampler for a GET request.
    'entry' is a dictionary containing:
      - url (full URL)
      - method (expected to be GET)
      - headers (dictionary of headers)
    Args:
      - entry: The request entry dictionary
      - hostname_var_map: Optional mapping from hostname to JMeter variable name for header parameterization
      - exclude_http2_pseudo_headers: If True, exclude HTTP/2 pseudo-headers from Header Manager (default: True)
      - testname_prefix: Optional prefix for the sampler name (e.g., "TC01_S01" for naming convention)
    Returns:
      - The HTTPSamplerProxy element
      - (Optionally) the HeaderManager element if headers exist, otherwise None.
    """
    url = entry.get("url", "")
    method = entry.get("method", "GET")
    headers = entry.get("headers", {})
    parsed_url = urllib.parse.urlparse(url)
    domain = parsed_url.netloc
    protocol = parsed_url.scheme if parsed_url.scheme else "http"
    # Include query string in path (JMeter expects full path with query params)
    base_path = parsed_url.path if parsed_url.path else "/"
    path_with_query = base_path + ('?' + parsed_url.query if parsed_url.query else '')
    
    # Build sampler name with optional prefix for naming convention
    base_name = f"{method.upper()} {base_path}"
    testname = f"{testname_prefix}_{base_name}" if testname_prefix else base_name
    testname = _sanitize_label(testname)
    
    sampler = ET.Element("HTTPSamplerProxy", attrib={
        "guiclass": "HttpTestSampleGui",
        "testclass": "HTTPSamplerProxy",
        "testname": testname
    })
    ET.SubElement(sampler, "stringProp", attrib={"name": "HTTPSampler.domain"}).text = domain
    ET.SubElement(sampler, "stringProp", attrib={"name": "HTTPSampler.protocol"}).text = protocol
    ET.SubElement(sampler, "stringProp", attrib={"name": "HTTPSampler.path"}).text = path_with_query
    ET.SubElement(sampler, "stringProp", attrib={"name": "HTTPSampler.method"}).text = method.upper()
    ET.SubElement(sampler, "stringProp", attrib={"name": "HTTPSampler.postBodyRaw"}).text = ""
    ET.SubElement(sampler, "boolProp", attrib={"name": "HTTPSampler.auto_redirects"}).text = "true"
    
    header_manager = create_header_manager(headers, hostname_var_map, exclude_http2_pseudo_headers) if headers else None
    return sampler, header_manager

# === HTTP Sampler for POST/PUT/DELETE Requests ===
def create_http_sampler_with_body(entry, hostname_var_map=None, exclude_http2_pseudo_headers=True, testname_prefix=None):
    """
    Creates an HTTP Sampler for POST/PUT/DELETE requests.
    'entry' should include:
      - url, method, headers, and post_data (the raw body)
    Args:
      - entry: The request entry dictionary
      - hostname_var_map: Optional mapping from hostname to JMeter variable name for header parameterization
      - exclude_http2_pseudo_headers: If True, exclude HTTP/2 pseudo-headers from Header Manager (default: True)
      - testname_prefix: Optional prefix for the sampler name (e.g., "TC01_S01" for naming convention)
    Returns:
      - The HTTPSamplerProxy element
      - (Optionally) the HeaderManager element if headers exist.
    """
    url = entry.get("url", "")
    method = entry.get("method", "POST")
    headers = entry.get("headers", {})
    post_data = entry.get("post_data", "")

    parsed_url = urllib.parse.urlparse(url)
    domain = parsed_url.netloc
    protocol = parsed_url.scheme if parsed_url.scheme else "http"
    # Include query string in path (JMeter expects full path with query params)
    base_path = parsed_url.path if parsed_url.path else "/"
    path_with_query = base_path + ('?' + parsed_url.query if parsed_url.query else '')

    # Build sampler name with optional prefix for naming convention
    base_name = f"{method.upper()} {base_path}"
    testname = f"{testname_prefix}_{base_name}" if testname_prefix else base_name
    testname = _sanitize_label(testname)

    # Create the HTTPSamplerProxy element
    sampler = ET.Element("HTTPSamplerProxy", attrib={
        "guiclass": "HttpTestSampleGui",
        "testclass": "HTTPSamplerProxy",
        "testname": testname
    })
    ET.SubElement(sampler, "stringProp", attrib={"name": "HTTPSampler.domain"}).text = domain
    ET.SubElement(sampler, "stringProp", attrib={"name": "HTTPSampler.protocol"}).text = protocol
    ET.SubElement(sampler, "stringProp", attrib={"name": "HTTPSampler.path"}).text = path_with_query
    ET.SubElement(sampler, "stringProp", attrib={"name": "HTTPSampler.method"}).text = method.upper()
    ET.SubElement(sampler, "boolProp", attrib={"name": "HTTPSampler.auto_redirects"}).text = "true"
    
    # ----------------------------
    # Here is the crucial part for raw body in JMeter:
    # ----------------------------
    # 1) Mark postBodyRaw as true
    ET.SubElement(sampler, "boolProp", attrib={"name": "HTTPSampler.postBodyRaw"}).text = "true"
    
    # 2) Create the HTTPsampler.Arguments element
    arguments_prop = ET.SubElement(sampler, "elementProp", attrib={
        "name": "HTTPsampler.Arguments",
        "elementType": "Arguments"
    })
    collection_prop = ET.SubElement(arguments_prop, "collectionProp", attrib={
        "name": "Arguments.arguments"
    })
    
    # 3) Create a single HTTPArgument to hold the raw body
    arg_element = ET.SubElement(collection_prop, "elementProp", attrib={
        "name": "",
        "elementType": "HTTPArgument"
    })
    # Do not automatically encode the body
    ET.SubElement(arg_element, "boolProp", attrib={"name": "HTTPArgument.always_encode"}).text = "false"
    # The actual body content
    ET.SubElement(arg_element, "stringProp", attrib={"name": "Argument.value"}).text = post_data
    # JMeter uses this to separate name/value pairs; for raw body, we just use "="
    ET.SubElement(arg_element, "stringProp", attrib={"name": "Argument.metadata"}).text = "="
    # Tells JMeter not to treat this as a name=value form
    ET.SubElement(arg_element, "boolProp", attrib={"name": "HTTPArgument.use_equals"}).text = "true"

    # Create a header manager if needed
    header_manager = create_header_manager(headers, hostname_var_map, exclude_http2_pseudo_headers) if headers else None

    return sampler, header_manager

# === Append Sampler to Parent HashTree ===
def append_sampler(parent, sampler, header_manager=None, extractors=None):
    """
    Appends a sampler and its children to the parent hashTree.
    JMeter expects that every sampler is immediately followed by a hashTree element.
    If a HeaderManager exists, it is placed inside its own hashTree.
    If extractors are provided, they are added as post-processors.
    
    Args:
        parent: The parent hashTree element (Thread Group or Controller)
        sampler: The HTTP Sampler element
        header_manager: Optional HeaderManager element
        extractors: Optional list of extractor elements (JSON, Regex, etc.)
    
    Returns:
        ET.Element: The sampler's hashTree (for further modifications if needed)
    """
    # Append the sampler
    parent.append(sampler)
    # Create a hashTree for the sampler's children.
    sampler_hash_tree = ET.Element("hashTree")
    
    # Add HeaderManager if present
    if header_manager is not None:
        sampler_hash_tree.append(header_manager)
        # Append an empty hashTree for the HeaderManager's children.
        header_hash_tree = ET.Element("hashTree")
        sampler_hash_tree.append(header_hash_tree)
    
    # Add extractors (post-processors) if present
    if extractors:
        for extractor in extractors:
            sampler_hash_tree.append(extractor)
            # Each extractor needs its own empty hashTree
            sampler_hash_tree.append(ET.Element("hashTree"))
    
    parent.append(sampler_hash_tree)
    return sampler_hash_tree

# === Flow Control Action (Test Action) Sampler ===
def create_flow_control_action(
    action_type: str = "pause",
    testname: str = "Think Time",
    duration: str = "${thinkTime}",
    target: int = 0
):
    """
    Creates a Test Action (Flow Control Action) element for JMeter 5.x.
    
    This is commonly used to add "Think Time" between steps to simulate
    realistic user behavior in end-to-end workflows.
    
    Args:
        action_type: The action to perform. Options:
            - "pause" (1): Pause for the specified duration
            - "stop" (0): Stop the current thread
            - "stop_now" (2): Stop the test now
            - "restart_next_loop" (3): Go to next iteration of current loop
        testname: Display name for the element (e.g., "Think Time")
        duration: Duration value in milliseconds. Can be:
            - A literal value like "5000"
            - A JMeter variable reference like "${thinkTime}"
        target: Target for the action (0 = current thread, 1 = all threads)
    
    Returns:
        The TestAction XML element
        
    Example XML output:
        <TestAction guiclass="TestActionGui" testclass="TestAction" testname="Think Time">
            <intProp name="ActionProcessor.action">1</intProp>
            <intProp name="ActionProcessor.target">0</intProp>
            <stringProp name="ActionProcessor.duration">${thinkTime}</stringProp>
        </TestAction>
    """
    # Map action types to JMeter numeric values
    action_map = {
        "stop": 0,
        "pause": 1,
        "stop_now": 2,
        "restart_next_loop": 3,
    }
    action_value = action_map.get(action_type.lower(), 1)  # Default to pause (1)
    
    # Create the TestAction element
    test_action = ET.Element("TestAction", attrib={
        "guiclass": "TestActionGui",
        "testclass": "TestAction",
        "testname": testname
    })
    
    # Add action property (intProp)
    ET.SubElement(test_action, "intProp", attrib={"name": "ActionProcessor.action"}).text = str(action_value)
    
    # Add target property (intProp) - 0 = current thread
    ET.SubElement(test_action, "intProp", attrib={"name": "ActionProcessor.target"}).text = str(target)
    
    # Add duration property (stringProp) - supports JMeter variables
    ET.SubElement(test_action, "stringProp", attrib={"name": "ActionProcessor.duration"}).text = duration
    
    return test_action


# === JSR223 Sampler ===
def create_jsr223_sampler(
    testname: str = "JSR223 Sampler",
    script: str = "",
    language: str = "groovy",
    cache_key: str = "true",
    parameters: str = ""
):
    """
    Creates a JSR223 Sampler element for custom scripting steps.

    JSR223 Samplers execute arbitrary scripts as a sampler (producing a
    SampleResult). Useful for data creation utilities, custom protocol
    calls, or any logic that doesn't fit standard HTTP samplers.

    Args:
        testname: Display name in JMeter.
        script: The script code to execute (Groovy recommended).
        language: Script language (default: "groovy").
        cache_key: Whether to cache the compiled script ("true" recommended).
        parameters: Optional parameters passed to the script.

    Returns:
        The JSR223Sampler XML element.
    """
    sampler = ET.Element("JSR223Sampler", attrib={
        "guiclass": "TestBeanGUI",
        "testclass": "JSR223Sampler",
        "testname": testname,
        "enabled": "true"
    })
    ET.SubElement(sampler, "stringProp", attrib={
        "name": "scriptLanguage"
    }).text = language
    ET.SubElement(sampler, "stringProp", attrib={
        "name": "parameters"
    }).text = parameters
    ET.SubElement(sampler, "stringProp", attrib={
        "name": "filename"
    }).text = ""
    ET.SubElement(sampler, "stringProp", attrib={
        "name": "cacheKey"
    }).text = cache_key
    ET.SubElement(sampler, "stringProp", attrib={
        "name": "script"
    }).text = script

    return sampler

# services/jmx/assertions.py
"""
JMeter Assertion Elements

This module contains functions to create JMeter assertion elements:
- Response Assertion (ResponseAssertion) - Validate response codes, bodies, headers
- Duration Assertion (DurationAssertion) - Enforce response time limits

Assertions are placed inside an HTTP Sampler's hashTree and evaluate
after the sampler runs, marking the sample as failed if the assertion
criteria are not met.
"""
import xml.etree.ElementTree as ET


def create_response_assertion(
    testname: str = "Response Assertion",
    field_to_test: str = "response_code",
    match_type: str = "equals",
    patterns: list = None,
    assume_success: bool = False
) -> ET.Element:
    """
    Creates a Response Assertion element.

    Validates response data against one or more patterns. Commonly used to
    verify HTTP status codes (e.g., 200) or check that the response body
    contains/excludes expected content.

    Args:
        testname: Display name in JMeter.
        field_to_test: Which part of the response to check:
            - "response_code" (default): HTTP status code
            - "response_message": HTTP reason phrase
            - "response_headers": Response headers text
            - "response_body": Full response body (alias: "body")
            - "request_headers": Request headers
            - "request_body": Request body
            - "url": Request URL
        match_type: How to compare patterns against the field:
            - "contains": Field contains the pattern (substring)
            - "matches": Field matches the pattern (full regex match)
            - "equals": Field equals the pattern exactly
            - "substring": Same as contains
            - "not_contains": Field does NOT contain the pattern
            - "not_matches": Field does NOT match the pattern
            - "not_equals": Field does NOT equal the pattern
        patterns: List of pattern strings to test against.
        assume_success: If True, set the initial assertion result to pass
            (useful with "not" match types).

    Returns:
        ET.Element: The ResponseAssertion XML element.
    """
    if patterns is None:
        patterns = ["200"]

    assertion = ET.Element("ResponseAssertion", attrib={
        "guiclass": "AssertionGui",
        "testclass": "ResponseAssertion",
        "testname": testname,
        "enabled": "true"
    })

    # Field to test mapping
    field_mapping = {
        "response_code": "Assertion.response_code",
        "response_message": "Assertion.response_message",
        "response_headers": "Assertion.response_headers",
        "response_body": "Assertion.response_data",
        "body": "Assertion.response_data",
        "request_headers": "Assertion.request_headers",
        "request_body": "Assertion.request_data",
        "url": "Assertion.sample_label",
    }
    field_value = field_mapping.get(field_to_test, "Assertion.response_code")

    ET.SubElement(assertion, "stringProp", attrib={
        "name": "Assertion.test_field"
    }).text = field_value

    # Match type mapping (bitmask values used by JMeter)
    match_mapping = {
        "contains": 2,          # CONTAINS (1 << 1)
        "substring": 2,         # alias for contains
        "matches": 1,           # MATCH (1 << 0)
        "equals": 8,            # EQUALS (1 << 3)
        "not_contains": 6,      # CONTAINS | NOT (2 | 4)
        "not_matches": 5,       # MATCH | NOT (1 | 4)
        "not_equals": 12,       # EQUALS | NOT (8 | 4)
    }
    match_value = match_mapping.get(match_type, 8)

    ET.SubElement(assertion, "intProp", attrib={
        "name": "Assertion.test_type"
    }).text = str(match_value)

    ET.SubElement(assertion, "boolProp", attrib={
        "name": "Assertion.assume_success"
    }).text = str(assume_success).lower()

    # Pattern collection
    collection = ET.SubElement(assertion, "collectionProp", attrib={
        "name": "Asserion.test_strings"
    })
    for pattern in patterns:
        ET.SubElement(collection, "stringProp", attrib={
            "name": str(hash(pattern))
        }).text = str(pattern)

    return assertion


def create_duration_assertion(
    testname: str = "Duration Assertion",
    max_duration_ms: int = 2000
) -> ET.Element:
    """
    Creates a Duration Assertion element.

    Marks the sample as failed if the response time exceeds the specified
    threshold. Useful as a guardrail to detect slow responses during
    performance testing.

    Args:
        testname: Display name in JMeter.
        max_duration_ms: Maximum allowed response time in milliseconds.

    Returns:
        ET.Element: The DurationAssertion XML element.
    """
    assertion = ET.Element("DurationAssertion", attrib={
        "guiclass": "DurationAssertionGui",
        "testclass": "DurationAssertion",
        "testname": testname,
        "enabled": "true"
    })
    ET.SubElement(assertion, "stringProp", attrib={
        "name": "DurationAssertion.duration"
    }).text = str(max_duration_ms)

    return assertion

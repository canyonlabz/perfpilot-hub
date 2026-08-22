"""
services/jmx/listeners.py

This module contains functions to create JMeter Listener components.
For example, it includes the function to generate a "View Results Tree" listener.
End-users and developers can extend or modify these functions independently
of the core JMeter component utilities.
"""

import xml.etree.ElementTree as ET
import datetime
import os
import urllib.parse
from xml.dom import minidom

# === View Results Tree Listener ===
# This function creates a JMeter "View Results Tree" listener element (ResultCollector)
def create_view_results_tree(listener_config):
    """
    Creates a JMeter "View Results Tree" listener element (ResultCollector)
    based on the provided listener_config dictionary.
    
    listener_config is expected to include:
      - save_response_data: boolean (maps to XML element "responseData")
      - save_request_headers: boolean (maps to XML element "requestHeaders")
      - save_response_headers: boolean (maps to XML element "responseHeaders")
      Optional keys:
      - filename: string, default "results_tree.csv"
      - TestPlan.comments: string, default "NOTE: This is a default Listener. Please configure to your needs."
      
    Returns:
      - The ResultCollector element (as an XML Element)
      - An accompanying empty hashTree element (required by JMeter)
    """
    
    # Create the ResultCollector element.
    result_collector = ET.Element("ResultCollector", attrib={
        "guiclass": "ViewResultsFullVisualizer",
        "testclass": "ResultCollector",
        "testname": "View Results Tree",
        "enabled": "true"
    })
    ET.SubElement(result_collector, "boolProp", {"name": "ResultCollector.error_logging"}).text = "false"
    
    # Create the objProp element holding the save configuration.
    obj_prop = ET.SubElement(result_collector, "objProp", {"name": "saveConfig"})
    value_elem = ET.SubElement(obj_prop, "value", {"class": "SampleSaveConfiguration"})
    
    # Set default properties (as per typical JMeter defaults) for the sample saving configuration.
    # We map our simplified YAML keys to the XML element tags.
    save_config_defaults = {
        "time": "true",
        "latency": "true",
        "timestamp": "true",
        "success": "true",
        "label": "true",
        "code": "true",
        "message": "true",
        "threadName": "true",
        "dataType": "true",
        "encoding": "false",
        "assertions": "false",
        "subresults": "true",
        "responseData": "false",      # Overridden by YAML: save_response_data
        "samplerData": "false",
        "xml": "false",
        "fieldNames": "true",
        "responseHeaders": "false",   # Overridden by YAML: save_response_headers
        "requestHeaders": "false",    # Overridden by YAML: save_request_headers
        "responseDataOnError": "false",
        "saveAssertionResultsFailureMessage": "true",
        "assertionsResultsToSave": "0",
        "bytes": "true",
        "sentBytes": "true",
        "url": "true",
        "threadCounts": "true",
        "sampleCount": "true",
        "idleTime": "true",
        "connectTime": "true"
    }
    
    # Update defaults using provided listener_config overrides.
    if "save_response_data" in listener_config:
        save_config_defaults["responseData"] = str(listener_config["save_response_data"]).lower()
    if "save_request_headers" in listener_config:
        save_config_defaults["requestHeaders"] = str(listener_config["save_request_headers"]).lower()
    if "save_response_headers" in listener_config:
        save_config_defaults["responseHeaders"] = str(listener_config["save_response_headers"]).lower()
    
    # Create an XML element for each property.
    for tag, val in save_config_defaults.items():
        child = ET.SubElement(value_elem, tag)
        child.text = val
    
    # Set filename (default "results_tree.csv") and TestPlan.comments.
    filename = listener_config.get("filename", "results_tree.csv")
    ET.SubElement(result_collector, "stringProp", {"name": "filename"}).text = filename

    comment = listener_config.get("TestPlan.comments", "NOTE: This is a default Listener. Please configure to your needs.")
    ET.SubElement(result_collector, "stringProp", {"name": "TestPlan.comments"}).text = comment

    # Return the listener element and an accompanying empty hashTree element.
    hash_tree = ET.Element("hashTree")
    return result_collector, hash_tree

# === Aggregate Results Listener ===
# This function creates a JMeter "Aggregate Report" listener element (ResultCollector)
def create_aggregate_report(listener_config):
    """
    Creates a JMeter "Aggregate Report" listener element (ResultCollector)
    based on the provided listener_config dictionary.
    
    listener_config: dict, expected to include (as booleans):
      - save_response_data  --> maps to XML tag <responseData>
      - save_request_headers  --> maps to XML tag <requestHeaders>
      - save_response_headers  --> maps to XML tag <responseHeaders>
    Optional keys:
      - filename: string (default "aggregate_report.csv")
      - TestPlan.comments: string (default "NOTE: This is a default Listener. Please configure to your needs.")
      
    Returns:
      - The ResultCollector XML element for the Aggregate Report.
      - An accompanying empty hashTree element (required by JMeter's XML structure).
    """
    # Create the ResultCollector element.
    aggregate_report = ET.Element("ResultCollector", attrib={
        "guiclass": "StatVisualizer",  # As per your XML snippet
        "testclass": "ResultCollector",
        "testname": "Aggregate Report",
        "enabled": "true"
    })
    ET.SubElement(aggregate_report, "boolProp", {"name": "ResultCollector.error_logging"}).text = "false"

    # Create the objProp element for save configuration.
    obj_prop = ET.SubElement(aggregate_report, "objProp", {"name": "saveConfig"})
    value_elem = ET.SubElement(obj_prop, "value", {"class": "SampleSaveConfiguration"})
    
    # Define the default settings based on your provided XML snippet.
    # Note: The keys here match the XML element names.
    defaults = {
        "time": "true",
        "latency": "true",
        "timestamp": "true",
        "success": "true",
        "label": "true",
        "code": "true",
        "message": "true",
        "threadName": "true",
        "dataType": "true",
        "encoding": "false",
        "assertions": "false",
        "subresults": "true",
        "responseData": "false",      # Overridden by YAML key: save_response_data
        "samplerData": "false",
        "xml": "false",
        "fieldNames": "true",
        "responseHeaders": "false",   # Overridden by YAML key: save_response_headers
        "requestHeaders": "false",    # Overridden by YAML key: save_request_headers
        "responseDataOnError": "false",
        "saveAssertionResultsFailureMessage": "true",
        "assertionsResultsToSave": "0",
        "bytes": "true",
        "sentBytes": "true",
        "url": "true",
        "threadCounts": "true",
        "sampleCount": "true",
        "idleTime": "true",
        "connectTime": "true"
    }
    
    # Override the defaults with values from listener_config.
    if "save_response_data" in listener_config:
        defaults["responseData"] = str(listener_config["save_response_data"]).lower()
    if "save_request_headers" in listener_config:
        defaults["requestHeaders"] = str(listener_config["save_request_headers"]).lower()
    if "save_response_headers" in listener_config:
        defaults["responseHeaders"] = str(listener_config["save_response_headers"]).lower()
    
    # Create an XML element for each configuration property.
    for tag, val in defaults.items():
        child = ET.SubElement(value_elem, tag)
        child.text = val

    # Add the filename property, with a default if not provided.
    filename = listener_config.get("filename", "aggregate_report.csv")
    ET.SubElement(aggregate_report, "stringProp", {"name": "filename"}).text = filename

    # Add the TestPlan.comments property.
    comment = listener_config.get("TestPlan.comments", "NOTE: This is a default Listener. Please configure to your needs.")
    ET.SubElement(aggregate_report, "stringProp", {"name": "TestPlan.comments"}).text = comment

    # Create an empty hashTree element (required by JMeter).
    hash_tree = ET.Element("hashTree")
    return aggregate_report, hash_tree

if __name__ == "__main__":
    # Quick test of the create_view_results_tree function.
    # Simulated listener configuration (as might be loaded from your YAML file).
    sample_listener_config = {
        "save_response_data": False,
        "save_request_headers": False,
        "save_response_headers": False,
        "filename": "results_tree.csv",
        "TestPlan.comments": "Default View Results Tree Listener; please adjust settings as needed."
    }
    listener_elem, listener_hash_tree = create_view_results_tree(sample_listener_config)
    xml_str = ET.tostring(listener_elem, encoding="utf-8")
    pretty_xml = minidom.parseString(xml_str).toprettyxml(indent="  ")
    print(pretty_xml)

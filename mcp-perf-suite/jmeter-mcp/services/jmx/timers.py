# services/jmx/timers.py
"""
JMeter Timer Elements

This module contains functions to create JMeter timer elements:
- Constant Timer (ConstantTimer) - Fixed delay between requests
- Constant Throughput Timer (ConstantThroughputTimer) - Enforce target throughput
- Gaussian Random Timer (GaussianRandomTimer) - Randomised delay for realistic pacing

Timers are placed inside a hashTree (at sampler, controller, or thread group level)
and introduce a delay before the next sampler in scope executes.
"""
import xml.etree.ElementTree as ET


def create_constant_timer(
    testname: str = "Constant Timer",
    delay_ms: str = "300"
) -> ET.Element:
    """
    Creates a Constant Timer element.

    Adds a fixed delay before the next sampler executes. Supports JMeter
    variables for dynamic think time (e.g., "${thinkTime}").

    Args:
        testname: Display name in JMeter.
        delay_ms: Delay in milliseconds (string to support JMeter variables).

    Returns:
        ET.Element: The ConstantTimer XML element.
    """
    timer = ET.Element("ConstantTimer", attrib={
        "guiclass": "ConstantTimerGui",
        "testclass": "ConstantTimer",
        "testname": testname,
        "enabled": "true"
    })
    ET.SubElement(timer, "stringProp", attrib={
        "name": "ConstantTimer.delay"
    }).text = str(delay_ms)

    return timer


def create_constant_throughput_timer(
    testname: str = "Constant Throughput Timer",
    target_throughput_per_min: str = "60.0",
    calc_mode: int = 0
) -> ET.Element:
    """
    Creates a Constant Throughput Timer element.

    Throttles execution to achieve a target throughput (samples per minute).
    Useful for steady-state load testing where a specific request rate
    must be maintained regardless of thread count.

    Args:
        testname: Display name in JMeter.
        target_throughput_per_min: Target throughput as samples per minute.
            Supports JMeter variables. Example: "100.0" means 100 requests/min.
        calc_mode: How throughput is calculated across threads:
            - 0: "this thread only" (each thread targets the throughput)
            - 1: "all active threads" (shared target across all threads)
            - 2: "all active threads in current thread group"
            - 3: "all active threads (shared)" (most common for load tests)
            - 4: "all active threads in current thread group (shared)"

    Returns:
        ET.Element: The ConstantThroughputTimer XML element.
    """
    timer = ET.Element("ConstantThroughputTimer", attrib={
        "guiclass": "TestBeanGUI",
        "testclass": "ConstantThroughputTimer",
        "testname": testname,
        "enabled": "true"
    })
    ET.SubElement(timer, "doubleProp", attrib={
        "name": "throughput"
    })
    # doubleProp uses a nested <value> element in JMeter 5.6+
    throughput_prop = timer.find("doubleProp[@name='throughput']")
    ET.SubElement(throughput_prop, "value").text = str(target_throughput_per_min)

    ET.SubElement(timer, "intProp", attrib={
        "name": "calcMode"
    }).text = str(calc_mode)

    return timer


def create_random_timer(
    testname: str = "Gaussian Random Timer",
    delay_ms: str = "300",
    range_ms: str = "100"
) -> ET.Element:
    """
    Creates a Gaussian Random Timer element.

    Introduces a randomised delay with a Gaussian (normal) distribution.
    The actual delay is a random value from the Gaussian distribution with
    the specified deviation, offset by the constant delay. This produces
    more realistic user think-time simulation than a fixed timer.

    Total delay = Gaussian(0, range_ms) + delay_ms

    Args:
        testname: Display name in JMeter.
        delay_ms: Constant offset (base delay) in milliseconds.
            Supports JMeter variables like "${thinkTime}".
        range_ms: Standard deviation of the Gaussian distribution in milliseconds.

    Returns:
        ET.Element: The GaussianRandomTimer XML element.
    """
    timer = ET.Element("GaussianRandomTimer", attrib={
        "guiclass": "GaussianRandomTimerGui",
        "testclass": "GaussianRandomTimer",
        "testname": testname,
        "enabled": "true"
    })
    ET.SubElement(timer, "stringProp", attrib={
        "name": "ConstantTimer.delay"
    }).text = str(delay_ms)
    ET.SubElement(timer, "stringProp", attrib={
        "name": "RandomTimer.range"
    }).text = str(range_ms)

    return timer

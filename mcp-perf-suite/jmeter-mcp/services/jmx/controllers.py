import xml.etree.ElementTree as ET
from typing import Tuple

# jmeter_controllers.py
# Factory methods for JMeter controllers (e.g. Simple, Transaction)

def create_simple_controller(testname: str = "Simple Controller") -> Tuple[ET.Element, ET.Element]:
    """
    Create a Simple Controller element with the given testname,
    plus an empty hashTree for its children.
    """
    controller = ET.Element("GenericController", attrib={
        "guiclass": "LogicControllerGui",
        "testclass": "GenericController",
        "testname": testname
    })
    hash_tree = ET.Element("hashTree")
    return controller, hash_tree


def create_transaction_controller(
    testname: str = "Transaction Controller",
    include_timers: bool = True
) -> Tuple[ET.Element, ET.Element]:
    """
    Create a Transaction Controller element with an accompanying hashTree.
    :param testname: Display name for the controller
    :param include_timers: Whether to include child timers in the transaction
    """
    controller = ET.Element("TransactionController", attrib={
        "guiclass": "TransactionControllerGui",
        "testclass": "TransactionController",
        "testname": testname
    })
    # add includeTimers property
    bool_prop = ET.SubElement(
        controller,
        "boolProp",
        name="TransactionController.includeTimers"
    )
    bool_prop.text = str(include_timers).lower()

    hash_tree = ET.Element("hashTree")
    return controller, hash_tree

def create_loop_controller(
    testname: str = "Loop Controller",
    loops: str = "1",
    continue_forever: bool = False
) -> Tuple[ET.Element, ET.Element]:
    """
    Create a Loop Controller element with an accompanying hashTree.

    Args:
        testname: Display name for the controller.
        loops: Number of iterations (string to support JMeter variables like "${loopCount}").
        continue_forever: If True, loop indefinitely (ignores loops value).

    Returns:
        Tuple of (LoopController element, empty hashTree for children).
    """
    controller = ET.Element("LoopController", attrib={
        "guiclass": "LoopControlPanel",
        "testclass": "LoopController",
        "testname": testname
    })
    ET.SubElement(
        controller, "stringProp",
        attrib={"name": "LoopController.loops"}
    ).text = str(loops)
    ET.SubElement(
        controller, "boolProp",
        attrib={"name": "LoopController.continue_forever"}
    ).text = str(continue_forever).lower()

    hash_tree = ET.Element("hashTree")
    return controller, hash_tree


def create_if_controller(
    testname: str = "If Controller",
    condition: str = "",
    evaluate_all: bool = False,
    use_expression: bool = True
) -> Tuple[ET.Element, ET.Element]:
    """
    Create an If Controller element with an accompanying hashTree.

    Args:
        testname: Display name for the controller.
        condition: Condition expression. Use __groovy() for Groovy expressions,
            e.g. '${__groovy(vars.get("product_id") != null)}'.
        evaluate_all: If True, evaluate condition for every child element
            (not just at controller entry).
        use_expression: If True, interpret the condition as a JMeter expression
            rather than a JavaScript snippet (recommended for JMeter >= 4.0).

    Returns:
        Tuple of (IfController element, empty hashTree for children).
    """
    controller = ET.Element("IfController", attrib={
        "guiclass": "IfControllerPanel",
        "testclass": "IfController",
        "testname": testname
    })
    ET.SubElement(
        controller, "stringProp",
        attrib={"name": "IfController.condition"}
    ).text = condition
    ET.SubElement(
        controller, "boolProp",
        attrib={"name": "IfController.evaluateAll"}
    ).text = str(evaluate_all).lower()
    ET.SubElement(
        controller, "boolProp",
        attrib={"name": "IfController.useExpression"}
    ).text = str(use_expression).lower()

    hash_tree = ET.Element("hashTree")
    return controller, hash_tree


def create_while_controller(
    testname: str = "While Controller",
    condition: str = ""
) -> Tuple[ET.Element, ET.Element]:
    """
    Create a While Controller element with an accompanying hashTree.

    The controller repeats its children while the condition evaluates to a
    non-empty string other than "false". Use __groovy() for complex logic,
    e.g. '${__groovy(vars.get("has_next") == "true")}'.

    Args:
        testname: Display name for the controller.
        condition: Condition expression that controls looping.

    Returns:
        Tuple of (WhileController element, empty hashTree for children).
    """
    controller = ET.Element("WhileController", attrib={
        "guiclass": "WhileControllerGui",
        "testclass": "WhileController",
        "testname": testname
    })
    ET.SubElement(
        controller, "stringProp",
        attrib={"name": "WhileController.condition"}
    ).text = condition

    hash_tree = ET.Element("hashTree")
    return controller, hash_tree


def create_once_only_controller(
    testname: str = "Once Only Controller"
) -> Tuple[ET.Element, ET.Element]:
    """
    Create a Once Only Controller element with an accompanying hashTree.

    Children of this controller execute only on the first iteration of the
    parent loop. Commonly used for login/setup steps that should run once
    per virtual user.

    Args:
        testname: Display name for the controller.

    Returns:
        Tuple of (OnceOnlyController element, empty hashTree for children).
    """
    controller = ET.Element("OnceOnlyController", attrib={
        "guiclass": "OnceOnlyControllerGui",
        "testclass": "OnceOnlyController",
        "testname": testname
    })

    hash_tree = ET.Element("hashTree")
    return controller, hash_tree


def create_switch_controller(
    testname: str = "Switch Controller",
    selection: str = "0"
) -> Tuple[ET.Element, ET.Element]:
    """
    Create a Switch Controller element with an accompanying hashTree.

    The Switch Controller runs exactly one of its children based on the
    selection value (zero-based index or child testname). Useful for
    routing virtual users through different business flow paths.

    Args:
        testname: Display name for the controller.
        selection: Zero-based index of the child to execute, or the testname
            of the target child. Supports JMeter variables like "${flowPath}".

    Returns:
        Tuple of (SwitchController element, empty hashTree for children).
    """
    controller = ET.Element("SwitchController", attrib={
        "guiclass": "SwitchControllerGui",
        "testclass": "SwitchController",
        "testname": testname
    })
    ET.SubElement(
        controller, "stringProp",
        attrib={"name": "SwitchController.value"}
    ).text = str(selection)

    hash_tree = ET.Element("hashTree")
    return controller, hash_tree


def create_foreach_controller(
    testname: str = "ForEach Controller",
    input_variable: str = "",
    output_variable: str = "",
    start_index: str = "0",
    end_index: str = "",
    use_separator: bool = True
) -> Tuple[ET.Element, ET.Element]:
    """
    Create a ForEach Controller element with an accompanying hashTree.

    Iterates over a set of JMeter variables produced by a previous extractor
    with match_number=-1 (all matches). For example, if a JSON extractor
    stores results as product_id_1, product_id_2, ..., this controller
    iterates and exposes each value as the output variable.

    Args:
        testname: Display name for the controller.
        input_variable: Base name of the input variable (e.g., "product_id").
            The controller reads product_id_1, product_id_2, etc.
        output_variable: Name of the loop variable exposed to children
            (e.g., "current_product_id").
        start_index: Starting index for iteration (typically "0").
        end_index: Ending index (empty string means iterate until no more
            variables are found).
        use_separator: If True, expects underscore separator between variable
            name and index (e.g., product_id_1). Should be True in most cases.

    Returns:
        Tuple of (ForeachController element, empty hashTree for children).
    """
    controller = ET.Element("ForeachController", attrib={
        "guiclass": "ForeachControlPanel",
        "testclass": "ForeachController",
        "testname": testname
    })
    ET.SubElement(
        controller, "stringProp",
        attrib={"name": "ForeachController.inputVal"}
    ).text = input_variable
    ET.SubElement(
        controller, "stringProp",
        attrib={"name": "ForeachController.returnVal"}
    ).text = output_variable
    ET.SubElement(
        controller, "stringProp",
        attrib={"name": "ForeachController.startIndex"}
    ).text = str(start_index)
    ET.SubElement(
        controller, "stringProp",
        attrib={"name": "ForeachController.endIndex"}
    ).text = str(end_index)
    ET.SubElement(
        controller, "boolProp",
        attrib={"name": "ForeachController.useSeparator"}
    ).text = str(use_separator).lower()

    hash_tree = ET.Element("hashTree")
    return controller, hash_tree

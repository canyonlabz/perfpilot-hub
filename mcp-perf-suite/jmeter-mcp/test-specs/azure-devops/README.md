# Azure DevOps Test Cases

This folder contains browser automation test cases exported from Azure DevOps (ADO). These are functional test steps written by the QA Functional team and serve as the **source input** for generating performance testing browser automation specs.

## Purpose

The QA Performance team can pull test cases from ADO work items (typically Test Cases within a PBI) and save them here as plain text files. These text files are then converted into Markdown-formatted browser automation specs that the JMeter MCP can consume for network traffic capture and JMeter script generation.

## Workflow

```
┌─────────────────────┐      ┌──────────────────────┐      ┌─────────────────────────┐
│  ADO Test Cases      │      │  This Folder           │      │  web-flows/              │
│  (QA Functional)     │ ──►  │  azure-devops/*.txt    │ ──►  │  <app>-<flow>.md         │
│                      │      │  (raw source)          │      │  (JMeter MCP ready)      │
└─────────────────────┘      └──────────────────────┘      └─────────────────────────┘
        Pull from ADO              Save as text file           Convert using Cursor Rules
```

1. **Pull** test case steps from an ADO work item (via ADO MCP or manual copy).
2. **Save** the raw steps as a `.txt` file in this folder.
3. **Convert** the text file into a Markdown spec by applying the `ado-to-jmeter-automation-conversion` Cursor Rules. The converted output is saved to `test-specs/web-flows/`.

## File Naming

Use a descriptive name that identifies the application and workflow:

```
<application>-<workflow-description>.txt
```

**Examples:**
- `app-login-and-dashboard.txt`
- `portal-create-new-record.txt`
- `platform-engagement-provisioning.txt`

## What These Files Typically Contain

ADO test cases may include:
- Numbered steps (often non-sequential due to edits or deletions)
- Section dividers or comment blocks (`// SECTION 1: ...`)
- Placeholder variables (`{{Company Name}}`, `{{User Email}}`)
- References to UI components (shadow-root, combobox, datepicker)
- Verification/assertion steps

## Conversion

The Cursor Rules file `ado-to-jmeter-automation-conversion.mdc` handles the transformation, including:
- Stripping ADO formatting artifacts (section comments, dividers)
- Renumbering steps sequentially (`Step 1:`, `Step 2:`, ...)
- Replacing placeholders with concrete test data
- Adding SSO/login and closing patterns required by the JMeter MCP spec parser

See `.cursor/rules/ado-to-jmeter-automation-conversion.mdc` for the full conversion rules.

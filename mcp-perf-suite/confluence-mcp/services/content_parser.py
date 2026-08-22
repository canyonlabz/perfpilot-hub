# Content parsing logic to convert markdown to confluence storage format (e.g XHTML)
import re
from typing import Union, Dict
from pathlib import Path
from fastmcp import Context
from utils.config import load_config

# Load the config.yaml which contains path folder settings. NOTE: OS specific yaml files will override default config.yaml
CONFIG = load_config()
CNF_CONFIG = CONFIG.get('confluence', {})
ARTIFACTS_PATH = CONFIG['artifacts']['artifacts_path']


async def markdown_to_confluence_xhtml(test_run_id: str, filename: str, ctx: Context = None, report_type: str = "single_run") -> Union[str, Dict]:
    """
    Converts a Markdown performance report to Confluence storage-format XHTML.
    
    Args:
        test_run_id: ID of the test run or comparison_id (used for artifact path).
        filename: Filename of the markdown report.
        ctx: FastMCP context for logging.
        report_type: Type of report - "single_run" (default) or "comparison".
            - "single_run": Path is artifacts/{test_run_id}/reports/
            - "comparison": Path is artifacts/comparisons/{test_run_id}/
    
    Returns:
        str: Flattened Confluence-compatible XHTML markup (newlines removed), 
             or error dict if conversion fails.
    
    Examples:
        # Single-run report
        markdown_to_confluence_xhtml("80247571", "performance_report_80247571.md", report_type="single_run")
        
        # Comparison report (test_run_id is comparison_id)
        markdown_to_confluence_xhtml("2026-01-21-09-03-30", "comparison_report_run1_run2.md", report_type="comparison")
    """
    # Construct path based on report_type
    if report_type == "comparison":
        # Comparison reports: artifacts/comparisons/{comparison_id}/
        markdown_path = Path(ARTIFACTS_PATH) / "comparisons" / test_run_id / filename
    else:
        # Single-run reports: artifacts/{test_run_id}/reports/
        markdown_path = Path(ARTIFACTS_PATH) / test_run_id / "reports" / filename
    file_path = Path(markdown_path)
    if not file_path.exists():
        error_msg = f"Markdown file not found: {markdown_path}"
        return {"error": error_msg}
    
    # Read markdown content
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            markdown_content = f.read()
    except Exception as e:
        error_msg = f"Failed to read markdown file: {e}"
        return {"error": error_msg}
    
    # Convert markdown to XHTML
    try:
        xhtml = _markdown_to_confluence_storage_format(markdown_content)
        
        # Write human-readable XHTML to file (with newlines preserved)
        xhtml_output_path = file_path.with_suffix('.xhtml')
        try:
            with open(xhtml_output_path, 'w', encoding='utf-8') as f:
                f.write(xhtml)
            if ctx:
                await ctx.info(f"Wrote human-readable XHTML to: {xhtml_output_path}")
        except Exception as e:
            # Log warning but don't fail conversion
            if ctx:
                await ctx.warning(f"Failed to write XHTML file: {e}")
        
        # Flatten XHTML for API submission (remove newlines)
        flattened_xhtml = _flatten_xhtml(xhtml)
        
        if ctx:
            await ctx.info(f"Successfully converted {file_path.name} to Confluence XHTML ({len(flattened_xhtml)} chars, flattened)")
        
        return flattened_xhtml
        
    except Exception as e:
        error_msg = f"Markdown conversion failed: {e}"
        return {"error": error_msg}

def _flatten_xhtml(xhtml: str) -> str:
    """
    Flattens XHTML by removing all newline characters.
    This is required for Confluence API submissions which fail with newlines in the payload.
    
    Args:
        xhtml: XHTML string with newlines.
    
    Returns:
        str: Flattened XHTML string with newlines removed.
    """
    # Remove all newline characters (\n, \r\n, \r)
    flattened = xhtml.replace('\r\n', '').replace('\n', '').replace('\r', '')
    return flattened

def _markdown_to_confluence_storage_format(markdown: str) -> str:
    """
    Internal function to convert markdown to Confluence storage XHTML.
    Handles headings, tables, bold, italic, lists, code blocks, and chart placeholders.
    
    Note: HTML comments (<!-- ... -->) are stripped from output as they should not
    be published to Confluence, though they remain in the source markdown file.
    """
    # Strip HTML comments before processing (single-line and multi-line)
    # This removes metadata comments like <!-- AI-REVISED REPORT ... -->
    markdown = re.sub(r'<!--[\s\S]*?-->', '', markdown)
    
    lines = markdown.split('\n')
    xhtml_lines = []
    in_table = False
    in_code_block = False
    code_block_content = []
    table_header_processed = False  # Track if we've processed the header row
    table_column_count = 0  # Track number of columns for colgroup
    
    for line in lines:
        # Handle code blocks
        if line.strip().startswith('```'):
            if not in_code_block:
                in_code_block = True
                code_block_content = []
                continue
            else:
                # End code block
                in_code_block = False
                xhtml_lines.append('<ac:structured-macro ac:name="code">')
                xhtml_lines.append('<ac:plain-text-body><![CDATA[')
                xhtml_lines.append('\n'.join(code_block_content))
                xhtml_lines.append(']]></ac:plain-text-body>')
                xhtml_lines.append('</ac:structured-macro>')
                continue
        
        if in_code_block:
            code_block_content.append(line)
            continue
        
        # Handle headings
        if line.startswith('# '):
            xhtml_lines.append(f'<h1>{_escape_html(line[2:].strip())}</h1>')
            continue
        elif line.startswith('## '):
            xhtml_lines.append(f'<h2>{_escape_html(line[3:].strip())}</h2>')
            continue
        elif line.startswith('### '):
            xhtml_lines.append(f'<h3>{_escape_html(line[4:].strip())}</h3>')
            continue
        elif line.startswith('#### '):
            xhtml_lines.append(f'<h4>{_escape_html(line[5:].strip())}</h4>')
            continue
        
        # Handle tables
        if '|' in line and line.strip().startswith('|'):
            # Parse cells first to get column count
            cells = [cell.strip() for cell in line.split('|')[1:-1]]  # Skip first/last empty
            
            if not in_table:
                in_table = True
                table_header_processed = False
                table_column_count = len(cells)
                xhtml_lines.append('<table>')
                # Generate dynamic colgroup based on actual column count
                colgroup_html = '<colgroup>'
                for i in range(table_column_count):
                    if i == 0:
                        colgroup_html += '<col style="min-width: 250px;" />'
                    else:
                        colgroup_html += '<col style="min-width: 120px;" />'
                colgroup_html += '</colgroup>'
                xhtml_lines.append(colgroup_html)
                xhtml_lines.append('<tbody>')
            
            # Skip separator lines (e.g., |---|---|) and mark header as processed
            if re.match(r'^\|[\s\-:]+\|', line):
                table_header_processed = True
                continue
            
            # Parse table row - use <th> for header row, <td> for data rows
            row_html = '<tr>'
            for idx, cell in enumerate(cells):
                cell_content = _apply_inline_formatting(cell)
                
                if not table_header_processed:
                    # Header row: use <th> with Cloud-compatible attributes
                    th_attrs = (
                        'rowspan="1" colspan="1" '
                        'colorname="Dark blue" '
                        'data-cell-background="#4c9aff" '
                        'aria-sort="none" '
                        'style="background-color: rgb(102, 157, 241);"'
                    )
                    # Header text styling: white text on colored background
                    th_content = (
                        f'<p><strong>'
                        f'<span data-renderer-mark="true" '
                        f'data-text-custom-color="#ffffff" '
                        f'class="fabric-text-color-mark" '
                        f'style="--custom-palette-color: var(--ds-text-inverse, #FFFFFF);">'
                        f'{cell_content}'
                        f'</span></strong></p>'
                    )
                    row_html += f'<th {th_attrs}>{th_content}</th>'
                else:
                    # Data row: use <td>
                    if idx == 0:
                        # First column: prevent text wrapping
                        row_html += f'<td style="white-space: nowrap;">{cell_content}</td>'
                    else:
                        row_html += f'<td>{cell_content}</td>'
            
            row_html += '</tr>'
            xhtml_lines.append(row_html)
            continue
        else:
            if in_table:
                in_table = False
                table_header_processed = False
                table_column_count = 0
                xhtml_lines.append('</tbody>')
                xhtml_lines.append('</table>')
        
        # Handle horizontal rules
        if line.strip() == '---':
            xhtml_lines.append('<hr />')
            continue
        
        # Handle unordered lists
        if line.strip().startswith('- '):
            content = _apply_inline_formatting(line.strip()[2:])
            xhtml_lines.append(f'<ul><li>{content}</li></ul>')
            continue
        
        # Handle chart placeholders - PRESERVE for later image substitution
        # New format: {{CHART_PLACEHOLDER: SCHEMA_ID}}
        if '{{CHART_PLACEHOLDER:' in line:
            # Keep placeholder as-is wrapped in <p> tags (will be replaced by update_page tool)
            xhtml_lines.append(f'<p>{line.strip()}</p>')
            continue
        
        # Legacy format: [CHART_PLACEHOLDER: ...] - convert to new format notice
        if '[CHART_PLACEHOLDER:' in line:
            # Warn about old format - should be updated to use {{}} format
            xhtml_lines.append(f'<p><em>[Legacy placeholder - update to curly brace format]</em></p>')
            continue
        
        # Handle blockquotes
        if line.strip().startswith('>'):
            content = _apply_inline_formatting(line.strip()[1:].strip())
            xhtml_lines.append(f'<blockquote><p>{content}</p></blockquote>')
            continue
        
        # Handle regular paragraphs (skip empty lines - they add no value in browser)
        if line.strip():
            content = _apply_inline_formatting(line.strip())
            xhtml_lines.append(f'<p>{content}</p>')
        # Empty lines are skipped - no output generated
    
    # Close any open table
    if in_table:
        xhtml_lines.append('</tbody>')
        xhtml_lines.append('</table>')
    
    return '\n'.join(xhtml_lines)

def _apply_inline_formatting(text: str) -> str:
    """
    Applies inline markdown formatting (bold, italic, code, links) to text.
    """
    # Escape HTML first
    text = _escape_html(text)
    
    # Inline code (`code`) - do this first so we don't treat markers inside code as formatting
    text = re.sub(r'`([^`]+?)`', r'<code>\1</code>', text)

    # Bold (**text**) - asterisk-based only
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # REMOVE this line if underscore-bold is not needed
    #text = re.sub(r'__(.+?)__', r'<strong>\1</strong>', text)
    
    # Italic (*text*) - asterisk-based only
    text = re.sub(r'\*([^\*]+?)\*', r'<em>\1</em>', text)
    # REMOVE this line to avoid eating underscores in identifiers
    #text = re.sub(r'_([^_]+?)_', r'<em>\1</em>', text)
    
    # Inline code (`code`)
    text = re.sub(r'`([^`]+?)`', r'<code>\1</code>', text)
    
    # Links [text](url) - convert to Confluence external link format
    # Pattern: [link text](url) -> <a href="url" class="external-link" rel="nofollow">link text</a>
    text = re.sub(
        r'\[([^\]]+)\]\(([^\)]+)\)',
        r'<a href="\2" class="external-link" rel="nofollow">\1</a>',
        text
    )
    
    return text

def _escape_html(text: str) -> str:
    """
    Escapes HTML special characters to prevent injection.
    """
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&#39;'))


# -----------------------------------------------
# Confluence Image XHTML Generation Functions
# -----------------------------------------------

def generate_confluence_image_xhtml(filename: str, height: int = 250) -> str:
    """
    Generate Confluence storage-format XHTML for an embedded image.
    
    Args:
        filename: Image filename (just the name, not full path).
        height: Image height in pixels (default 250).
    
    Returns:
        str: Confluence ac:image XHTML markup.
    
    Note:
        Does NOT use ac:align attribute to avoid float behavior.
        Confluence will left-align by default, with content flowing below.
    
    Example:
        >>> generate_confluence_image_xhtml("CPU_UTILIZATION_MULTILINE.png", 300)
        '<ac:image ac:height="300"><ri:attachment ri:filename="CPU_UTILIZATION_MULTILINE.png" /></ac:image>'
    """
    return f'<ac:image ac:height="{height}"><ri:attachment ri:filename="{filename}" /></ac:image>'


def replace_chart_placeholders(xhtml: str, chart_mapping: dict, default_height: int = 250) -> str:
    """
    Replace chart placeholders in XHTML with ac:image markup.
    
    This function finds all {{CHART_PLACEHOLDER: SCHEMA_ID}} markers in the XHTML
    and replaces them with the appropriate Confluence image macro XHTML.
    
    Args:
        xhtml: XHTML content with {{CHART_PLACEHOLDER: ID}} markers.
        chart_mapping: Dict mapping chart_id to a list of {filename, height} dicts.
            Supports multiple images per chart_id for per-resource charts
            (e.g., CPU_CORES_LINE with separate images per K8s service).
            Example:
            {
                "CPU_UTILIZATION_MULTILINE": [
                    {"filename": "CPU_UTILIZATION_MULTILINE.png", "height": 300}
                ],
                "CPU_CORES_LINE": [
                    {"filename": "CPU_CORES_LINE-web.png", "height": 250},
                    {"filename": "CPU_CORES_LINE-worker.png", "height": 250}
                ]
            }
        default_height: Default image height if not specified in mapping.
    
    Returns:
        str: XHTML with placeholders replaced by ac:image markup.
        
    Note:
        - Placeholders without a matching chart in chart_mapping are left unchanged.
        - The replacement wraps each image in a <p> tag for proper formatting.
        - Per-resource charts produce one <p><ac:image.../></p> block per service.
    """
    # Pattern: {{CHART_PLACEHOLDER: CHART_ID}} - captures the ID
    # Allows for optional whitespace around the colon and ID
    pattern = r'\{\{CHART_PLACEHOLDER:\s*([A-Z0-9_]+)\s*\}\}'
    
    def replacer(match):
        chart_id = match.group(1)
        if chart_id in chart_mapping:
            entries = chart_mapping[chart_id]
            image_tags = []
            for info in entries:
                filename = info.get('filename', f'{chart_id}.png')
                height = info.get('height', default_height)
                image_tags.append(generate_confluence_image_xhtml(filename, height))
            return '\n'.join(image_tags)
        else:
            # Keep placeholder if no mapping found (will show as text in final page)
            return match.group(0)
    
    return re.sub(pattern, replacer, xhtml)


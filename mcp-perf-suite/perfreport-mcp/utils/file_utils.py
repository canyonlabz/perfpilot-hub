"""
utils/file_utils.py
File I/O helper functions for PerfAnalysis MCP outputs
"""
import json
import asyncio
import pypandoc
from pathlib import Path
from typing import Dict, Optional, List

# -----------------------------------------------
# File I/O helper functions
# ----------------------------------------------- 
async def _load_json_safe(
    path: Path,
    key: str,
    source_dict: Dict,
    warnings: List,
    missing_sections: List
) -> Optional[Dict]:
    """Load JSON file safely with error handling"""
    try:
        if path.exists():
            data = await _load_json_file(path)
            source_dict[key] = str(path)
            return data
        else:
            warnings.append(f"{key} file not found: {path}")
            missing_sections.append(key)
            return None
    except Exception as e:
        warnings.append(f"Error loading {key}: {str(e)}")
        missing_sections.append(key)
        return None


async def _load_text_safe(
    path: Path,
    key: str,
    source_dict: Dict,
    warnings: List
) -> Optional[str]:
    """Load text file safely"""
    try:
        if path.exists():
            content = await _load_text_file(path)
            source_dict[key] = str(path)
            return content
        else:
            warnings.append(f"{key} file not found: {path}")
            return None
    except Exception as e:
        warnings.append(f"Error loading {key}: {str(e)}")
        return None


async def _load_json_file(path: Path) -> Dict:
    """Load JSON file"""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


async def _load_text_file(path: Path) -> str:
    """Load text file"""
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


async def _save_text_file(path: Path, content: str):
    """Save text file"""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


async def _save_json_file(path: Path, data: Dict):
    """Save JSON file"""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


async def _convert_to_pdf(md_path: Path, output_dir: Path, run_id: str) -> Path:
    """Convert Markdown to PDF using pypandoc"""
    try:        
        pdf_path = output_dir / f"performance_report_{run_id}.pdf"
        pypandoc.convert_file(
            str(md_path),
            'pdf',
            outputfile=str(pdf_path),
            extra_args=['--pdf-engine=xelatex']
        )
        return pdf_path
    except Exception as e:
        raise Exception(f"PDF conversion failed: {str(e)}")


async def _convert_to_docx(md_path: Path, output_dir: Path, run_id: str) -> Path:
    """Convert Markdown to Word using pypandoc"""
    try:       
        docx_path = output_dir / f"performance_report_{run_id}.docx"
        pypandoc.convert_file(
            str(md_path),
            'docx',
            outputfile=str(docx_path)
        )
        return docx_path
    except Exception as e:
        raise Exception(f"DOCX conversion failed: {str(e)}")

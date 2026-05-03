"""
Markdown to PDF conversion tool for MCP server.

Converts Markdown files to styled PDF documents using markdown and WeasyPrint.
"""

import sys
import os
import markdown
from pathlib import Path


async def handle_md_to_pdf(request_id: str, args: dict, _tool_response, **kwargs) -> dict:
    """Convert a Markdown file to a styled PDF document using WeasyPrint.
    
    Args:
        request_id: Unique identifier for the MCP request
        args: Dictionary containing parameters:
            - source_path (str, required): Path to the source Markdown file
            - output_path (str, optional): Path for the output PDF (defaults to same name with .pdf extension)
            - css_path (str, optional): Path to a custom CSS stylesheet
            - verbose (bool, optional): Enable verbose/debug output
        _tool_response: Helper function to format responses
        **kwargs: Additional keyword arguments
        
    Returns:
        Formatted response with the path to the generated PDF or error message
    """
    try:
        # Get parameters
        source_path = args.get("source_path", "")
        output_path = args.get("output_path", "")
        css_path = args.get("css_path", "")
        verbose = args.get("verbose", False)
        
        if not source_path:
            return _tool_response(request_id, "Error: 'source_path' is required.")
        
        # Resolve paths relative to resources directory
        base = Path("resources")
        source_file = (base / source_path).resolve()
        
        if not source_file.exists():
            return _tool_response(request_id, f"Error: Source file '{source_file}' does not exist.")
        
        # Determine output path
        if not output_path:
            output_file = source_file.with_suffix('.pdf')
        else:
            output_file = (base / output_path).resolve()
        
        # Read and convert Markdown to HTML
        with open(source_file, 'r', encoding='utf-8') as f:
            md_text = f.read()
        
        html_body = markdown.markdown(md_text, extensions=['extra', 'sane_lists'])
        
        if verbose:
            print(f"[DEBUG] Parsed HTML (first 2000 chars):\n{html_body[:2000]}")
            print(f"[DEBUG] HTML length: {len(html_body)} chars\n")
        
        # Build HTML wrapper
        full_html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
                        <body>{html_body}</body></html>"""
        
        # Write temporary HTML file
        temp_html = Path("/tmp/md_to_pdf_temp.html")
        with open(temp_html, 'w', encoding='utf-8') as f:
            f.write(full_html)
        
        if verbose:
            print(f"[DEBUG] Temp HTML written to: {temp_html}")
        
        # Import WeasyPrint
        from weasyprint import HTML, CSS
        
        # Build base URL for resolving relative paths (like CSS)
        base_url = str(base.resolve())
        
        # Prepare stylesheets list
        stylesheets = []
        
        if css_path:
            css_file = (base / css_path).resolve()
            if css_file.exists():
                if verbose:
                    print(f"[DEBUG] Using CSS file: {css_file}")
                stylesheets.append(CSS(filename=str(css_file)))
            else:
                if verbose:
                    print(f"[DEBUG] CSS file not found: {css_file}, generating without custom styles")
        else:
            if verbose:
                print(f"[DEBUG] No CSS file specified, using default styles")
        
        # Create HTML object and generate PDF
        html_obj = HTML(filename=str(temp_html), base_url=base_url)
        html_obj.write_pdf(str(output_file), stylesheets=stylesheets)
        
        # Clean up temp file
        if temp_html.exists():
            temp_html.unlink()
        
        msg = f"Successfully converted '{source_path}' to '{output_path or source_file.with_suffix('.pdf').name}'."
        if verbose:
            print(f"[DEBUG] Output: {output_file}\n")
        
        return _tool_response(request_id, msg)
        
    except ImportError:
        return _tool_response(request_id, "Error: WeasyPrint is not installed. Install with: pip install weasyprint")
    except Exception as e:
        return _tool_response(request_id, f"Error converting Markdown to PDF: {str(e)}")

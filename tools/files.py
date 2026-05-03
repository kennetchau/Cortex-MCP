"""
File operations module for MCP server.

Provides tools for listing, reading, writing, appending, replacing,
and inserting text in files within the sandboxed resources directory.
"""

import pathlib
import os


# Directories/files to skip when building tree view
_SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode", ".mypy_cache", ".pytest_cache", "node_modules", ".venv", "venv"}


def _build_tree(root: pathlib.Path, prefix: str = "", is_last: bool = True, is_root: bool = False) -> str:
    """Recursively build a tree-style string representation of a directory.
    
    Args:
        root: The path to list (file or directory)
        prefix: Leading indentation prefix for this level
        is_last: Whether this is the last item at its level
        is_root: Whether this is the top-level call
        
    Returns:
        Formatted tree string
    """
    lines = []
    name = root.name
    
    if is_root:
        # Root level — no connector needed
        icon = "📁 " if root.is_dir() else "📄 "
        lines.append(f"{icon}{name}")
        child_prefix = ""
    else:
        # Subsequent levels — use tree connectors
        connector = "└── " if is_last else "├── "
        icon = "📁 " if root.is_dir() else "📄 "
        lines.append(f"{prefix}{connector}{icon}{name}")
        child_prefix = prefix + ("    " if is_last else "│   ")
    
    if root.is_dir():
        try:
            children = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            # Filter out skipped directories
            children = [c for c in children if c.name not in _SKIP_DIRS]
            
            for i, child in enumerate(children):
                is_last_child = (i == len(children) - 1)
                subtree = _build_tree(child, child_prefix, is_last_child)
                if subtree:
                    lines.append(subtree)
        except PermissionError:
            lines.append(f"{child_prefix}└── 🔒 (permission denied)")
    
    return "\n".join(lines)


async def handle_list_files(request_id: str, args: dict, _tool_response, **kwargs) -> dict:
    """List files and directories in a specified folder.
    
    Args:
        request_id: Unique identifier for the MCP request
        args: Dictionary containing 'path' and optional 'recursive' parameters
        _tool_response: Helper function to format responses
        **kwargs: Additional keyword arguments
        
    Returns:
        Formatted response with file/directory listing or tree view
    """
    base = pathlib.Path("resources")
    target = (base / args.get("path", "")).resolve()

    if not str(target).startswith(str(base.resolve())):
        return _tool_response(request_id, f"Error: Path '{args.get('path')}' escapes 'resources' directory.")

    if not target.exists():
        return _tool_response(request_id, f"Error: Path '{target}' does not exist.")

    recursive = args.get("recursive", False)

    if recursive:
        # Build tree view
        result = _build_tree(target, is_root=True)
        return _tool_response(request_id, result)
    else:
        # Flat listing (original behavior)
        items = []
        for p in sorted(target.iterdir()):
            prefix = "📁 " if p.is_dir() else "📄 "
            items.append(f"{prefix}{p.name}")

        return _tool_response(request_id, "\n".join(items) if items else "Directory is empty.")


async def handle_read_file(request_id: str, args: dict, _tool_response, **kwargs) -> dict:
    """Read a file with AI-friendly line-based windowing.
    
    Returns formatted output with navigation hints for paging through large files.
    
    Args:
        request_id: Unique identifier for the MCP request
        args: Dictionary containing 'path', 'line_start', 'line_count' parameters
        _tool_response: Helper function to format responses
        **kwargs: Additional keyword arguments
        
    Returns:
        Formatted file content with line numbers and navigation hints
    """
    # Strict sandbox enforcement using realpath
    base = pathlib.Path("resources").resolve()
    target = (base / args.get("path", "")).resolve()

    # Verify the resolved path is strictly within sandbox
    if not str(target).startswith(str(base) + "/") and target != base:
        return _tool_response(request_id,
            f"Error: Path '{args.get('path')}' escapes the 'resources' directory.")

    if not target.is_file():
        return _tool_response(request_id,
            f"Error: File not found: '{target.name}'")

    # AI-friendly parameters: line-based windowing
    line_start = int(args.get("line_start", 1))      # 1-indexed
    line_count = int(args.get("line_count", 50))     # number of lines

    try:
        content = target.read_text(encoding="utf-8")
        lines = content.split('\n')
        total_lines = len(lines)

        # Handle negative line numbers (count from end)
        if line_start < 0:
            line_start = max(1, total_lines + line_start + 1)

        # Clamp to valid range
        line_start = max(1, min(line_start, total_lines))

        # Get the window
        end_line = min(line_start + line_count - 1, total_lines)
        window_lines = lines[line_start - 1:end_line]

        # Build result with clear formatting
        result = f"📄 {target.name} ({total_lines} lines)\n"
        result += "─" * 50 + "\n"
        result += f"Lines {line_start}-{end_line}:\n\n"

        for i, line in enumerate(window_lines, start=line_start):
            result += f"{i:6d} │ {line}\n"

        result += "\n" + "─" * 50 + "\n"

        # Navigation hints
        if end_line < total_lines:
            next_start = end_line + 1
            result += f"\n➡️ Next: `line_start={next_start}` to continue"
        if line_start > 1:
            prev_end = line_start - 1
            result += f"\n⬅️ Previous: `line_start=1, line_count={prev_end}` for beginning"
        if total_lines > line_count and line_count > 0:
            mid = (total_lines // 2)
            result += f"\n📍 Middle: `line_start={mid}, line_count={line_count}`"

        return _tool_response(request_id, result)

    except UnicodeDecodeError:
        return _tool_response(request_id,
            f"Error: '{target.name}' appears to be a binary file.")
    except Exception as e:
        return _tool_response(request_id,
            f"Error reading file: {str(e)}")


async def handle_write_file(request_id: str, args: dict, _tool_response, **kwargs) -> dict:
    """Write text content to a file. Creates parent directories if needed.
    
    Args:
        request_id: Unique identifier for the MCP request
        args: Dictionary containing 'path' and 'content' parameters
        _tool_response: Helper function to format responses
        **kwargs: Additional keyword arguments
        
    Returns:
        Confirmation message with character count
    """
    base = pathlib.Path("resources")
    target = (base / args.get("path", "")).resolve()

    if not str(target).startswith(str(base.resolve())):
        return _tool_response(request_id, f"Error: Path '{args.get('path')}' escapes 'resources' directory.")

    content = args.get("content", "")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return _tool_response(request_id, f"Successfully wrote {len(content)} characters to '{target.name}'.")
    except Exception as e:
        return _tool_response(request_id, f"Error writing file: {str(e)}")


async def handle_append_to_file(request_id: str, args: dict, _tool_response, **kwargs) -> dict:
    """Append text content to the end of an existing file without overwriting.
    
    Args:
        request_id: Unique identifier for the MCP request
        args: Dictionary containing 'path' and 'content' parameters
        _tool_response: Helper function to format responses
        **kwargs: Additional keyword arguments
        
    Returns:
        Confirmation message with character count
    """
    base = pathlib.Path("resources")
    target = (base / args.get("path", "")).resolve()

    if not str(target).startswith(str(base.resolve())):
        return _tool_response(request_id, f"Error: Path '{args.get('path')}' escapes 'resources' directory.")

    content = args.get("content", "")
    try:
        # Ensure parent dir exists
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(content)
        return _tool_response(request_id, f"Successfully appended {len(content)} characters to '{target.name}'.")
    except Exception as e:
        return _tool_response(request_id, f"Error appending to file: {str(e)}")


async def handle_replace_in_file(request_id: str, args: dict, _tool_response, **kwargs) -> dict:
    """Find and replace specific text in a file. If multiple occurrences exist, replaces all.
    
    Args:
        request_id: Unique identifier for the MCP request
        args: Dictionary containing 'path', 'old_text', and 'new_text' parameters
        _tool_response: Helper function to format responses
        **kwargs: Additional keyword arguments
        
    Returns:
        Confirmation message with replacement count
    """
    base = pathlib.Path("resources")
    target = (base / args.get("path", "")).resolve()

    if not str(target).startswith(str(base.resolve())):
        return _tool_response(request_id, f"Error: Path '{args.get('path')}' escapes 'resources' directory.")

    old_text = args.get("old_text", "")
    new_text = args.get("new_text", "")

    try:
        content = target.read_text(encoding="utf-8")
        if old_text not in content:
            return _tool_response(request_id, f"Marker text not found in '{target.name}'.")

        new_content = content.replace(old_text, new_text)
        count = content.count(old_text)  # Count replacements

        target.write_text(new_content, encoding="utf-8")
        return _tool_response(request_id, f"Replaced {count} occurrence(s) of text in '{target.name}'.")
    except UnicodeDecodeError:
        return _tool_response(request_id, f"Error: '{target.name}' is a binary file.")
    except Exception as e:
        return _tool_response(request_id, f"Error replacing text: {str(e)}")


async def handle_insert_after_marker(request_id: str, args: dict, _tool_response, **kwargs) -> dict:
    """Insert text after a specific marker line in a file. Creates the file if it doesn't exist.
    
    Args:
        request_id: Unique identifier for the MCP request
        args: Dictionary containing 'path', 'marker', and 'content' parameters
        _tool_response: Helper function to format responses
        **kwargs: Additional keyword arguments
        
    Returns:
        Confirmation message
    """
    base = pathlib.Path("resources")
    target = (base / args.get("path", "")).resolve()

    if not str(target).startswith(str(base.resolve())):
        return _tool_response(request_id, f"Error: Path '{args.get('path')}' escapes 'resources' directory.")

    marker = args.get("marker", "")
    content = args.get("content", "")

    try:
        # Create file if it doesn't exist
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text("", encoding="utf-8")

        file_content = target.read_text(encoding="utf-8")
        lines = file_content.splitlines(True)  # Keep line endings

        inserted = False
        new_lines = []
        for line in lines:
            new_lines.append(line)
            if marker in line and not inserted:
                new_lines.append(content)
                inserted = True

        if not inserted:
            return _tool_response(request_id, f"Marker '{marker}' not found in '{target.name}'. File unchanged.")

        target.write_text("".join(new_lines), encoding="utf-8")
        return _tool_response(request_id, f"Successfully inserted text after marker in '{target.name}'.")
    except Exception as e:
        return _tool_response(request_id, f"Error inserting text: {str(e)}")

async def handle_search_files(request_id: str, args: dict, _tool_response, **kwargs) -> dict:
    """Search for text patterns within files in the resources directory.
    
    Args:
        request_id: Unique identifier for the MCP request
        args: Dictionary containing 'query', optional 'path', 'pattern_type' ('exact' or 'regex'),
              and optional 'max_results' parameters
        _tool_response: Helper function to format responses
        **kwargs: Additional keyword arguments
        
    Returns:
        Formatted response with matching file paths, line numbers, and context
    """
    import re
    
    query = args.get("query", "")
    if not query:
        return _tool_response(request_id, "Error: 'query' parameter is required.")
    
    base = pathlib.Path("resources").resolve()
    search_path = (base / args.get("path", "")).resolve()
    
    if not str(search_path).startswith(str(base)):
        return _tool_response(request_id, f"Error: Path '{args.get('path')}' escapes 'resources' directory.")
    
    if not search_path.exists():
        return _tool_response(request_id, f"Error: Path '{search_path}' does not exist.")
    
    pattern_type = args.get("pattern_type", "exact")
    max_results = int(args.get("max_results", 20))
    
    # Compile regex if needed
    try:
        if pattern_type == "regex":
            compiled_pattern = re.compile(query, re.IGNORECASE)
        else:
            compiled_pattern = re.compile(re.escape(query), re.IGNORECASE)
    except re.error as e:
        return _tool_response(request_id, f"Invalid regex pattern: {e}")
    
    matches = []
    files_searched = 0
    
    # Walk the directory tree
    for root, dirs, files in os.walk(search_path):
        # Skip hidden and common noise directories
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith('.')]
        
        for filename in sorted(files):
            filepath = pathlib.Path(root) / filename
            
            # Skip binary files by extension
            if filepath.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', 
                                            '.pdf', '.zip', '.tar', '.gz', '.exe', '.dll', '.so'}:
                continue
            
            files_searched += 1
            
            try:
                content = filepath.read_text(encoding="utf-8", errors="ignore")
                lines = content.split('\n')
                
                for line_num, line in enumerate(lines, start=1):
                    if compiled_pattern.search(line):
                        # Extract context: the matching line plus 2 lines before and after
                        start_ctx = max(0, line_num - 3)
                        end_ctx = min(len(lines), line_num + 2)
                        context_lines = lines[start_ctx:end_ctx]
                        
                        matches.append({
                            "file": str(filepath.relative_to(base)),
                            "line": line_num,
                            "context": "\n".join(f"  {i+start_ctx+1} │ {l[:150]}" 
                                               for i, l in enumerate(context_lines))
                        })
                        
                        if len(matches) >= max_results:
                            break
            except (UnicodeDecodeError, PermissionError, OSError):
                continue
            
            if len(matches) >= max_results:
                break
        
        if len(matches) >= max_results:
            break
    
    if not matches:
        return _tool_response(request_id, 
            f"No matches found. Searched {files_searched} file(s) in '{search_path}'.")
    
    result = f"Found {len(matches)} match(es) in {files_searched} file(s):\n\n"
    result += "─" * 60 + "\n"
    
    for i, match in enumerate(matches, 1):
        result += f"\n📄 {match['file']} (line {match['line']})\n"
        result += match['context'] + "\n"
        if i < len(matches):
            result += "─" * 60 + "\n"
    
    return _tool_response(request_id, result)

async def handle_delete_file(request_id: str, args: dict, _tool_response, **kwargs) -> dict:
    """Delete a file from the resources directory.
    
    Args:
        request_id: Unique identifier for the MCP request
        args: Dictionary containing 'path' parameter
        _tool_response: Helper function to format responses
        **kwargs: Additional keyword arguments
        
    Returns:
        Confirmation message or error
    """
    base = pathlib.Path("resources")
    target = (base / args.get("path", "")).resolve()

    if not str(target).startswith(str(base.resolve())):
        return _tool_response(request_id, f"Error: Path '{args.get('path')}' escapes 'resources' directory.")

    if not target.exists():
        return _tool_response(request_id, f"File not found: '{target.name}'.")

    if target.is_dir():
        return _tool_response(request_id, f"'{target.name}' is a directory. Use remove_directory instead.")

    try:
        target.unlink()
        return _tool_response(request_id, f"Successfully deleted '{target.name}'.")
    except Exception as e:
        return _tool_response(request_id, f"Error deleting file: {str(e)}")


async def handle_remove_directory(request_id: str, args: dict, _tool_response, **kwargs) -> dict:
    """Remove a directory from the resources directory.
    
    Args:
        request_id: Unique identifier for the MCP request
        args: Dictionary containing 'path' and optional 'recursive' parameters
        _tool_response: Helper function to format responses
        **kwargs: Additional keyword arguments
        
    Returns:
        Confirmation message or error
    """
    import shutil
    
    base = pathlib.Path("resources")
    target = (base / args.get("path", "")).resolve()

    if not str(target).startswith(str(base.resolve())):
        return _tool_response(request_id, f"Error: Path '{args.get('path')}' escapes 'resources' directory.")

    if not target.exists():
        return _tool_response(request_id, f"Directory not found: '{target.name}'.")

    if not target.is_dir():
        return _tool_response(request_id, f"'{target.name}' is a file. Use delete_file instead.")

    recursive = args.get("recursive", False)

    try:
        if recursive:
            shutil.rmtree(target)
            return _tool_response(request_id, f"Successfully removed directory '{target.name}' and all contents.")
        else:
            if any(target.iterdir()):
                return _tool_response(request_id, f"Directory '{target.name}' is not empty. Use recursive=True to remove contents.")
            target.rmdir()
            return _tool_response(request_id, f"Successfully removed empty directory '{target.name}'.")
    except Exception as e:
        return _tool_response(request_id, f"Error removing directory: {str(e)}")
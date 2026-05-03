import asyncio
import os
from pathlib import Path

from config import BASE_DIR


def _get_home_dir():
    """Get home directory dynamically. Tries env var first, then derives from BASE_DIR."""
    # Try HOME environment variable first
    home = os.environ.get("HOME")
    if home:
        return home
    
    # Derive from BASE_DIR (parent of project root)
    try:
        return str(BASE_DIR.parent)
    except Exception:
        return "/home/user"  # Generic fallback


async def handle_run_command(request_id: str, args: dict, _tool_response, **kwargs) -> dict:
    """Execute a shell command within the sandbox directory."""
    command = args.get("command", "")
    cwd = args.get("cwd", ".")
    timeout = int(args.get("timeout", 15))
    max_output = int(args.get("max_output", 5000))

   # Consistent sandbox base like other tools
    base = BASE_DIR.resolve()
    target_cwd = (base / cwd).resolve()

    # Strict containment check
    if not str(target_cwd).startswith(str(base)):
        return _tool_response(request_id, f"Error: CWD '{cwd}' escapes the sandbox directory.")

    # Get home directory dynamically
    home_dir = _get_home_dir()
    
    # Strict isolation bubblewrap sandbox
    bwrap_cmd = [
        "bwrap",
        "--setenv", "PATH", f"/usr/local/bin:/usr/bin:{home_dir}/.local/bin",
        "--setenv", "HOME", home_dir,
        "--setenv", "UV_CACHE_DIR", "/tmp/uv-cache",
        "--ro-bind", "/", "/",           # Root filesystem read-only
        # Removed --tmpfs /etc so DNS resolution works
        "--tmpfs", "/tmp",               # Disposable temp (disappears on exit)
        "--tmpfs", "/var/tmp",           # Disposable temp
        "--bind", str(base), str(base),  # Mount resources rw inside sandbox
        "--dev", "/dev",                 # Device access (needed for python/tty)
        "--proc", "/proc",               # Procfs for process info
        "--die-with-parent",             # Clean up if parent dies
        "--chdir", str(target_cwd),      # Set working directory
        "/bin/sh", "-c", command        # Execute command in sandboxed shell
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *bwrap_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=max_output
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        out_text = stdout.decode("utf-8", errors="replace")[:max_output]
        err_text = stderr.decode("utf-8", errors="replace")[:max_output]

        status = "OK" if proc.returncode == 0 else f"Exited with code {proc.returncode}"
        return _tool_response(request_id, f"[{status}]\nSTDOUT:\n{out_text}\nSTDERR:\n{err_text}")
    except asyncio.TimeoutError:
        return _tool_response(request_id, f"Command timed out after {timeout}s")
    except FileNotFoundError:
        return _tool_response(request_id, "Error: 'bwrap' not found. Install bubblewrap.")
    except Exception as e:
        return _tool_response(request_id, f"Execution error: {str(e)}")

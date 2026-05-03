# Tools package - contains all MCP tool implementations
from .today import handle_today
from .add import handle_add
from .web_research import handle_web_search, handle_fetch_content
from .files import (
    handle_list_files,
    handle_read_file,
    handle_write_file,
    handle_append_to_file,
    handle_replace_in_file,
    handle_insert_after_marker,
    handle_search_files,
    handle_delete_file,
    handle_remove_directory,
)
from .run_command import handle_run_command
from .md_to_pdf import handle_md_to_pdf
from .sqlite_store import (
    handle_store_context,
    handle_query_context,
    handle_clear_context,
    handle_list_projects,
    handle_add_context_alias,
)

__all__ = [
    "handle_today",
    "handle_add",
    "handle_web_search",
    "handle_fetch_content",
    "handle_list_files",
    "handle_read_file",
    "handle_write_file",
    "handle_append_to_file",
    "handle_replace_in_file",
    "handle_insert_after_marker",
    "handle_search_files",
    "handle_delete_file",
    "handle_remove_directory",
    "handle_run_command",
    "handle_md_to_pdf",
    "handle_store_context",
    "handle_query_context",
    "handle_clear_context",
    "handle_list_projects",
    "handle_add_context_alias",
]

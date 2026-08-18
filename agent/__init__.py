"""Cross-platform server health & backup monitoring agent.

The agent collects host health metrics (via :mod:`psutil`) plus evidence of the
server's most recent backup, then pushes them as JSON to the central portal's
ingestion API on a fixed interval. When the backend is unreachable the payload
is buffered to a local SQLite queue and re-sent later, so no data is lost.

See ``AGENT_SPEC.md`` for the authoritative build specification.
"""

__version__ = "1.0.0"

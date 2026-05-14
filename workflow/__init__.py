"""User-defined workflows auto-discovered by wf_engine.

Each ``.py`` module in this package is treated as one workflow. Modules
expose declarative metadata: ``WORKFLOW_KEY`` (optional), ``get_input_schema()``
(optional), and ``get_nodes()`` (required). The engine reads these on startup
to construct ``Workflow`` objects — no register API calls needed.
"""

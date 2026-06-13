# app/__init__.py
"""
Composition root for the strangler-fig harness.

This package provides an ADDITIVE alternative wiring of the existing concrete
adapters (``pipeline.llm_client.LLMClient``, ``audit.logger.AuditLogger``,
``integrations.jira_client.JiraClient``) behind the ports declared in
``core.ports``. It introduces no DI framework — just a ``build_container``
factory that returns a small ``Container`` holding the wired ports.

Nothing here changes existing call sites or behavior; ``main.py`` and
``ui/server.py`` continue to construct their dependencies exactly as before.
"""

from app.container import Container, LocalObjectStore, build_container

__all__ = ["Container", "LocalObjectStore", "build_container"]

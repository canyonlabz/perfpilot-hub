"""Persistence layer for the PerfPilot agent framework.

All database CRUD operations, connection pool management, and domain-level
data access live here. Each store module wraps a single table or logical
aggregate from the ``perfagent_state`` PostgreSQL database.
"""

# PerfMemory Knowledge Graph — Apache AGE

This directory contains SQL scripts for the Apache AGE graph layer that supplements
pgvector semantic search with structural relationship traversal.

## Prerequisites

- PostgreSQL 18 with Apache AGE extension installed
- pgvector extension enabled
- `debug_sessions` and `debug_attempts` tables created (via `schema/schema_openai.sql`
  or `schema/schema_ollama.sql`)

## Scripts

| File | Purpose | When to Run |
|------|---------|-------------|
| `001_create_graph.sql` | Creates the `perf_knowledge` graph and vertex labels (Attempt, Project, Service, ErrorPattern, FixPattern) | Once, after AGE extension is installed |
| `002_seed_graph_from_existing_data.sql` | Migrates existing debug_attempts into graph nodes/edges | Once, only if you have existing data before enabling the graph layer |

### Migration Scripts (in `sql/migrations/`)

| File | Purpose | When to Run |
|------|---------|-------------|
| `002_update_graph_schema.sql` | Adds Service vertex label and alias property to Project nodes | Once, for existing databases upgrading to taxonomy support |

## Graph Structure

> **Terminology:** In the graph layer, a "Project" node maps one-to-one to an
> "application" in the taxonomy YAML and the `system_under_test` column in the
> relational database. When documentation or code refers to "project," it means
> the application being performance tested.

### Vertex Labels

| Label | Description | Key Properties |
|-------|-------------|----------------|
| `Attempt` | 1:1 with a `debug_attempts` row | `attempt_id`, `project`, `error_category`, `fix_type`, `outcome`, `response_code`, `component_type` |
| `Project` | One per application (system_under_test) | `name`, `alias` |
| `Service` | One per distinct microservice within a project | `name`, `application` |
| `ErrorPattern` | One per distinct `(error_category, response_code)` pair | `error_category`, `response_code` |
| `FixPattern` | One per distinct `(fix_type, component_type)` pair | `fix_type`, `component_type` |

### Edge Labels

| Edge | Direction | Description |
|------|-----------|-------------|
| `BELONGS_TO` | Attempt -> Project | Every attempt belongs to a project |
| `HAS_SERVICE` | Project -> Service | A project contains one or more services |
| `TARGETS_SERVICE` | Attempt -> Service | An attempt targets a specific service |
| `HAS_ERROR` | Attempt -> ErrorPattern | When `error_category` is not null |
| `FIXED_BY` | Attempt -> FixPattern | When `outcome = 'resolved'` and `fix_type` is not null |
| `SIMILAR_TO` | Attempt -> Attempt | Cross-project structural or embedding-based similarity |

### SIMILAR_TO Edge Properties

| Property | Type | Description |
|----------|------|-------------|
| `match_type` | TEXT | `'embedding'`, `'error_pattern'`, `'fix_pattern'`, or `'composite'` |
| `cross_project` | BOOL | `true` if the two attempts are from different projects |
| `similarity` | FLOAT | Cosine similarity score (for embedding-based edges) |

## Configuration

The graph layer is controlled by `config.yaml`:

```yaml
graph:
  enabled: true
  graph_name: perf_knowledge
  vector_weight: 0.6
  graph_weight: 0.4
  embedding_edge_threshold: 0.82
  max_embedding_edges: 3
```

Set `enabled: false` to disable all graph operations without removing AGE.

## Validation

After running `001_create_graph.sql`:

```sql
SELECT * FROM ag_catalog.ag_graph;
SELECT * FROM ag_catalog.ag_label;
```

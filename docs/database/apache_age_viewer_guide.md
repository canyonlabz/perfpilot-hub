# Apache AGE-Viewer — Graph Visualization Guide

Apache AGE-Viewer is a web-based visualization tool for exploring the PerfMemory knowledge graph. It connects to your PostgreSQL + Apache AGE database and renders Cypher query results as interactive node-edge diagrams.

> **Prerequisites:**
> - PostgreSQL 18+ with Apache AGE extension running (see [Apache AGE Installation Guide](apache_age_installation_guide.md))
> - Graph schema applied (`001_create_graph.sql`)
> - AGE-Viewer installed and connected to your `perfmemory` database
> - AGE-Viewer project: [https://github.com/apache/age-viewer](https://github.com/apache/age-viewer)

---

## Graph Overview

The `perf_knowledge` graph contains four node types and four edge types:

| Node Label | Description |
|---|---|
| `Attempt` | A single debug iteration (maps 1:1 to a `debug_attempts` row) |
| `Project` | A system under test (e.g., "Shopping Cart", "SSO") |
| `ErrorPattern` | A distinct `(error_category, response_code)` pair |
| `FixPattern` | A distinct `(fix_type, component_type)` pair |

| Edge Label | Direction | Description |
|---|---|---|
| `BELONGS_TO` | Attempt -> Project | Every attempt belongs to a project |
| `HAS_ERROR` | Attempt -> ErrorPattern | Links attempt to its error classification |
| `FIXED_BY` | Attempt -> FixPattern | Links resolved attempts to the fix that worked |
| `SIMILAR_TO` | Attempt -> Attempt | Cross-project similarity (structural or embedding-based) |

---

## Queries

### Node Counts

Quick check on what's in the graph:

```sql
SELECT * FROM cypher('perf_knowledge', $$
    MATCH (a:Attempt) RETURN count(a)
$$) AS (count agtype);
```

```sql
SELECT * FROM cypher('perf_knowledge', $$
    MATCH ()-[e]->() RETURN count(e)
$$) AS (count agtype);
```

### All Projects

```sql
SELECT * FROM cypher('perf_knowledge', $$
    MATCH (p:Project) RETURN p
$$) AS (v agtype);
```

### All Error Patterns

```sql
SELECT * FROM cypher('perf_knowledge', $$
    MATCH (ep:ErrorPattern) RETURN ep
$$) AS (v agtype);
```

### All Fix Patterns

```sql
SELECT * FROM cypher('perf_knowledge', $$
    MATCH (fp:FixPattern) RETURN fp
$$) AS (v agtype);
```

---

### Project Membership

All attempts grouped by project:

```sql
SELECT * FROM cypher('perf_knowledge', $$
    MATCH (a:Attempt)-[e:BELONGS_TO]->(p:Project)
    RETURN a, e, p
$$) AS (a agtype, e agtype, p agtype);
```

Attempts for a specific project:

```sql
SELECT * FROM cypher('perf_knowledge', $$
    MATCH (a:Attempt)-[e:BELONGS_TO]->(p:Project {name: 'Shopping Cart'})
    RETURN a, e, p
$$) AS (a agtype, e agtype, p agtype);
```

---

### Error Pattern Relationships

All attempts linked to their error patterns:

```sql
SELECT * FROM cypher('perf_knowledge', $$
    MATCH (a:Attempt)-[e:HAS_ERROR]->(ep:ErrorPattern)
    RETURN a, e, ep
$$) AS (a agtype, e agtype, ep agtype);
```

Error patterns shared across different projects (the cross-project connections that make graph search valuable):

```sql
SELECT * FROM cypher('perf_knowledge', $$
    MATCH (a1:Attempt)-[:HAS_ERROR]->(ep:ErrorPattern)<-[:HAS_ERROR]-(a2:Attempt)
    WHERE a1.project <> a2.project
    RETURN a1, ep, a2
$$) AS (a1 agtype, ep agtype, a2 agtype);
```

---

### Fix Pattern Relationships

All resolved attempts and the fix patterns that resolved them:

```sql
SELECT * FROM cypher('perf_knowledge', $$
    MATCH (a:Attempt)-[e:FIXED_BY]->(fp:FixPattern)
    RETURN a, e, fp
$$) AS (a agtype, e agtype, fp agtype);
```

---

### Cross-Project Similarity

All SIMILAR_TO edges (structural or embedding-based):

```sql
SELECT * FROM cypher('perf_knowledge', $$
    MATCH (a:Attempt)-[s:SIMILAR_TO]->(b:Attempt)
    RETURN a, s, b
$$) AS (a agtype, s agtype, b agtype);
```

---

### Single Attempt Neighborhood

Explore all connections for a specific attempt (replace the UUID with any `attempt_id`):

```sql
SELECT * FROM cypher('perf_knowledge', $$
    MATCH (a:Attempt {attempt_id: '246a22fa-0e26-4729-b207-613eb208dac1'})-[e]-(n)
    RETURN a, e, n
$$) AS (a agtype, e agtype, n agtype);
```

---

### Full Graph Visualization

Render the entire graph (use `LIMIT` to control density):

```sql
SELECT * FROM cypher('perf_knowledge', $$
    MATCH (n)-[e]->(m)
    RETURN n, e, m
    LIMIT 200
$$) AS (n agtype, e agtype, m agtype);
```

---

## Tips

- **Start small:** Begin with the Project or ErrorPattern queries before rendering the full graph. Large graphs can be hard to read in the viewer.
- **Use the neighborhood query** to drill into a specific attempt after finding it via `find_similar_attempts` or `find_cross_project_patterns` in the PerfMemory MCP tools.
- **Cross-project error patterns** is the most informative visualization — it shows exactly where the graph layer adds value over vector-only search.
- **LIMIT clause:** For large graphs, add `LIMIT` to keep the visualization manageable. Start with 50-100 and increase as needed.

---

## Troubleshooting

For installation and startup issues, see [AGE-Viewer Troubleshooting](troubleshooting/age-viewer-troubleshooting.md). Covers macOS and Windows:

- `ERR_OSSL_EVP_UNSUPPORTED` with Node.js 18+
- Missing `@babel/runtime` backend dependency
- Broken `cytoscape/src/util` import (known AGE-Viewer bug)
- Backend connection failures after restart
- Apple Silicon build issues

---

## Related Docs

- [Apache AGE Installation Guide](apache_age_installation_guide.md) — Database and graph schema setup
- [pgvector Installation Guide](pgvector_installation_guide.md) — Base PostgreSQL + pgvector setup
- [PerfMemory README](../perfmemory-mcp/README.md) — MCP server documentation and tool reference
- [Graph Schema Reference](../perfmemory-mcp/sql/graph/README.md) — Vertex/edge label details

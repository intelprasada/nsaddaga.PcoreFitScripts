# Query Recipes

All queries are exposed via REST. Filters are composable: AND across keys,
OR within a multi-valued key (`?owner=alice,bob` = alice OR bob).

## 1. Tasks for a user
```
GET /api/tasks?owner=alice
GET /api/tasks?owner=alice&hide_done=1
```

## 2. Agenda — next 7 days for a user
```
GET /api/agenda?owner=alice&days=7
```
Returns tasks where `eta ∈ [today, today+7d]` and `status != done`,
sorted by `eta ASC, priority_rank ASC, created_at ASC`,
**grouped by day** for direct rendering.

## 3. Filter by feature (cross-user pull)
```
GET /api/features/search-rewrite/tasks
```
Returns the feature's tasks **plus** an aggregation envelope:
```json
{
  "tasks": [...],
  "aggregation": {
    "owners":     ["alice", "bob"],
    "projects":   ["veganotes", "platform"],
    "eta_range":  ["2026-04-19", "2026-05-12"],
    "status_breakdown":   {"todo": 4, "in-progress": 2, "done": 7},
    "priority_breakdown": {"P0": 1, "P1": 5, "P2": 7}
  }
}
```

## 4. By due date — same aggregation envelope
```
GET /api/tasks?eta_after=2026-04-19&eta_before=2026-05-01
```

## 5. Bidirectional links
```
GET /api/cards/{id}/links?direction=both
```
Merges outgoing (`#task` / `#link` from this card) and incoming references
(other cards mentioning this one) using the SQL view `links_bidir`. Each
returned row has a `direction: "in" | "out"` field — used by the Graph view
and the "Related" panel on every card.

## 6. Composable filters
```
GET /api/tasks?owner=alice,bob&priority=P0,P1&project=veganotes&hide_done=1
```

## 7. Full-text search
```
GET /api/search?q=login%20screen
```
SQLite FTS5 over `notes(title, body_md)`; can be combined with filter overlay.

## 8. Saved views
Every filter combination is URL-encodable. Pin frequently-used queries; they
are stored per-user in `users.saved_views_json`.

## Reference SQL — agenda

```sql
SELECT t.id, t.title,
       eta.value_norm AS eta,
       COALESCE(pri.value_norm, 999) AS pri_rank
FROM tasks t
JOIN task_owners o ON o.task_id = t.id
JOIN users u       ON u.id = o.user_id AND u.name = :user
JOIN task_attrs eta ON eta.task_id = t.id AND eta.key = 'eta'
LEFT JOIN task_attrs pri ON pri.task_id = t.id AND pri.key = 'priority'
WHERE t.status != 'done'
  AND eta.value_norm BETWEEN :today AND :today_plus_7
ORDER BY eta.value_norm ASC, pri_rank ASC, t.created_at ASC;
```

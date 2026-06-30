WORKFLOW_TASK_RESOLVED_STATUSES = frozenset({"completed", "approved", "rejected", "cancelled"})
WORKFLOW_TASK_STATUSES = WORKFLOW_TASK_RESOLVED_STATUSES | frozenset({"pending_user", "blocked"})

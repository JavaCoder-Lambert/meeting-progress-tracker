"""Field provenance retained inside draft JSON, outside the model output schema."""

TASK_DEFAULTS = {"status": "not_started", "priority": "normal", "progress": 0}


def task_provided_fields(item):
    if "_provided_fields" in item:
        provided = set(item["_provided_fields"])
    else:
        # Older drafts cannot distinguish explicit neutral values from model defaults.
        provided = {key for key, value in item.items()
                    if key not in TASK_DEFAULTS or value != TASK_DEFAULTS[key]}
    if item.get("status") == "done" and "status" in provided:
        provided.add("progress")
    return provided


def task_review_values(item, task):
    values = dict(item)
    provided = task_provided_fields(item)
    for field, default in TASK_DEFAULTS.items():
        values[field] = getattr(task, field) if task and field not in provided else item.get(field, default)
    return values

"""Wave's display column preferences for each work definition."""

def configure_columns(manager, columns):
    """Set the Wave columns for a registered set of jobs before preview."""
    with manager._cond:
        _store_columns(manager, _validated_columns(manager, columns))


def _configured_columns(manager):
    return getattr(manager, "_wave_columns", None)


def _store_columns(manager, columns):
    manager._wave_columns = columns


def _validated_columns(manager, columns):
    if not isinstance(columns, (list, tuple)) or not all(isinstance(c, str) for c in columns):
        raise TypeError("columns must be a sequence of names")
    if columns and not any(manager.flow_for(job) is not None for job in manager.jobs):
        raise ValueError("unknown display column")
    return tuple(dict.fromkeys(columns))


def _available_columns(workspace, jobs, snapshots):
    columns = {}
    data_by_job = {snapshot["id"]: snapshot["data"] for snapshot in snapshots}
    for job in jobs:
        flow = workspace.flow_for(job)
        if flow is not None and job.id in data_by_job:
            aliases = {}
            for spec in (*flow.parsers, *flow.analyzers):
                for name in spec.outputs:
                    columns[f"{spec.name}.{name}"] = None
                    aliases[name] = aliases.get(name, 0) + 1
            columns.update(dict.fromkeys(name for name, count in aliases.items()
                                         if count == 1 and "." not in name))
            columns.update(dict.fromkeys(data_by_job[job.id]))
    return list(columns)


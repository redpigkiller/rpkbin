# Job Manager

`rpkbin.job_manager` runs Python callables and shell commands with explicit job lifecycle, status, result, error, and wait handling.

```python
from rpkbin.job_manager import FuncJob, JobManager

job = FuncJob("hello", lambda: "ok")
with JobManager(max_workers=1) as manager:
    manager.add(job)
    manager.wait()
print(job.status, job.result)  # done ok
```

Use it for local concurrent work. Choose Wave when you also need parsers, hooks, PTY support, logs, or a TUI.

- Full API: [Job Manager reference](job_manager.md)
- 中文入口：[README_zh.md](README_zh.md)
- Focused tests: `python -m pytest tests/job_manager -q`

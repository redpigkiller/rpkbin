# Job Manager

`rpkbin.job_manager` 以明確的 job lifecycle、status、result、error 與 wait 控制 Python callable 和 shell command。

```python
from rpkbin.job_manager import FuncJob, JobManager

job = FuncJob("hello", lambda: "ok")
with JobManager(max_workers=1) as manager:
    manager.add(job)
    manager.wait()
print(job.status, job.result)  # done ok
```

它適合本機並行工作；若還需要 parser、hooks、PTY、logs 或 TUI，請改看 Wave。

- 完整 API：[Job Manager reference](job_manager_zh.md)
- English entry：[README.md](README.md)
- Focused tests：`python -m pytest tests/job_manager -q`

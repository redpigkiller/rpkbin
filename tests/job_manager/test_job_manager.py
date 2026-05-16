import os
import shutil
import time
import uuid
import tempfile
import threading
import logging
import io
import pytest

from rpkbin.job_manager import JobManager, CmdJob, FuncJob, DONE, FAILED, PENDING, CANCELLED, RUNNING
import rpkbin.job_manager.manager as manager_mod

def test_cmd_job():
    job = CmdJob("test_cmd", "echo hello")
    with JobManager(max_workers=1) as manager:
        manager.add(job)
        manager.wait()
    
    assert job.status == DONE
    assert job.result == 0
    assert "hello" in "\n".join(job.logs())

def test_func_job():
    def my_func(a, b):
        return a + b

    job = FuncJob("test_func", my_func, args=(1, 2))
    with JobManager(max_workers=1) as manager:
        manager.add(job)
        manager.wait()

    assert job.status == DONE
    assert job.result == 3

def test_job_cancel():
    def long_func():
        time.sleep(1)
        return "yes"

    job = FuncJob("long", long_func)
    with JobManager(max_workers=2) as manager:
        manager.add(job)
        time.sleep(0.1)
        job.cancel()
        manager.wait()

    assert job.status == CANCELLED
    
def test_dynamic_resources():
    flag = {"gpu": 0}
    
    def my_func():
        return "ok"

    job = FuncJob("test", my_func, resources={"gpu": 1})
    with JobManager(max_workers=2, resources={"gpu": lambda: flag["gpu"]}) as manager:
        manager.add(job)
        time.sleep(0.2)
        assert job.status == "pending" # Resource 0, can't fit
        
        flag["gpu"] = 1 # give resource
        manager.wait(job.id, timeout=2.0)
        assert job.status == DONE

def test_job_retry():
    attempts = 0
    def flaky_func():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ValueError("Not yet")
        return "success"

    job = FuncJob("flaky", flaky_func, max_retries=3)
    with JobManager(max_workers=1) as manager:
        manager.add(job)
        manager.wait()
        
    assert job.status == DONE
    assert job.result == "success"
    assert attempts == 3

def test_job_callbacks():
    log_called = False
    done_called = False
    fail_called = False
    watch_called = False
    
    job = CmdJob("cb_job", "echo Hello World")
    
    def on_log(j, line):
        nonlocal log_called
        if "Hello" in line:
            log_called = True
            
    def on_done(j):
        nonlocal done_called
        done_called = True
        
    def on_fail(j, e):
        nonlocal fail_called
        fail_called = True
        
    def on_watch(j, m):
        nonlocal watch_called
        watch_called = True
    
    job.on_log(on_log)
    job.on_done(on_done)
    job.on_fail(on_fail)
    job.watch("World", on_watch)
    
    with JobManager(max_workers=1) as manager:
        manager.add(job)
        manager.wait()
        
    assert job.status == DONE
    assert log_called
    assert done_called
    assert not fail_called
    assert watch_called

def test_manager_pause_resume():
    job1 = FuncJob("j1", lambda: time.sleep(0.1))
    job2 = FuncJob("j2", lambda: time.sleep(0.1))
    
    with JobManager(max_workers=1) as manager:
        manager.pause()
        manager.add(job1)
        manager.add(job2)
        
        time.sleep(0.1)
        assert len(manager.pending()) == 2
        assert len(manager.running()) == 0
        
        manager.resume()
        manager.wait()
        
    assert job1.status == DONE
    assert job2.status == DONE

def test_cmd_job_failure():
    # Run a command that definitely fails
    job = CmdJob("fail_job", "exit 1")
    with JobManager(max_workers=1) as manager:
        manager.add(job)
        manager.wait()
        
    assert job.status == FAILED
    assert job.result != 0

def test_on_queue_drained():
    drained = False
    def on_drained(mgr):
        nonlocal drained
        drained = True
        
    with JobManager(max_workers=1) as manager:
        manager.on_queue_drained(on_drained)
        manager.add(CmdJob("quick", "echo 1"))
        manager.wait()
        
    # Wait slightly for event bus to fire
    time.sleep(0.1)
    assert drained

def test_manager_cancel_all():
    job1 = FuncJob("j1", lambda: time.sleep(0.5))
    job2 = FuncJob("j2", lambda: time.sleep(0.5))
    
    with JobManager(max_workers=1) as manager:
        manager.add(job1)
        manager.add(job2)
        time.sleep(0.1) # Let j1 start
        manager.cancel_all()
        manager.wait()
        
    assert job1.status == CANCELLED
    assert job2.status == CANCELLED

def test_update_config():
    ev = threading.Event()
    job1 = FuncJob("j1", lambda: ev.wait(timeout=2.0))
    job2 = FuncJob("j2", lambda: "ok")
    
    with JobManager(max_workers=1) as manager:
        manager.add(job1)
        manager.add(job2)
        time.sleep(0.1)
        assert len(manager.pending()) == 1
        
        manager.update_config(max_workers=2)
        manager.wait(job2.id, timeout=1.0)
        assert job2.status == DONE
        
        ev.set()
        manager.wait()

def test_manager_stats():
    ev1 = threading.Event()
    ev2 = threading.Event()
    
    job1 = FuncJob("j1", lambda: ev1.wait(timeout=2.0), resources={"gpu": 1})
    job2 = FuncJob("j2", lambda: ev2.wait(timeout=2.0), resources={"gpu": 1})
    
    with JobManager(max_workers=4, resources={"gpu": 2}) as manager:
        manager.add(job1)
        manager.add(job2)
        time.sleep(0.2) # Allow both to start
        
        st = manager.stats()
        assert st["workers"]["used"] == 2
        assert st["workers"]["total"] == 4
        assert st["resources"]["gpu"]["used"] == 2
        assert st["resources"]["gpu"]["total"] == 2
        assert st["jobs"]["running"] == 2
        
        ev1.set()
        ev2.set()
        manager.wait()

def test_custom_job_id():
    my_id = uuid.uuid4()
    job1 = CmdJob("cmd", "echo 1", job_id=my_id)
    job2 = FuncJob("func", lambda: 1, job_id=str(my_id))
    
    assert job1.id == my_id
    assert job2.id == my_id

def test_cmd_send_input():
    # Use a temp script to ensure reliable input() behavior across OS/CI
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write("print('ready', flush=True)\nx = input()\nprint(f'got:{x}', flush=True)\n")
        script_path = f.name
        
    try:
        job = CmdJob("interactive", f'python "{script_path}"')
        with JobManager(max_workers=1) as manager:
            manager.add(job)
            
            # Wait for 'ready'
            t0 = time.time()
            ready = False
            while time.time() - t0 < 3.0:
                if any("ready" in line for line in job.logs()):
                    ready = True
                    break
                time.sleep(0.1)
                
            assert ready, "Job did not print 'ready' in time"
            
            # Send input
            job.send_input("secret123\n")
            
            # Wait for finish
            assert manager.wait(job.id, timeout=3.0), "Job did not finish after input"
            
        assert job.status == DONE
        assert any("got:secret123" in line for line in job.logs())
    finally:
        os.remove(script_path)

def test_flush_tokens():
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        # Loop to ensure it doesn't drop the script too fast. Print prompt without newline.
        f.write("import time, sys\nprint('ucli% ', end='')\nsys.stdout.flush()\ntime.sleep(2)\n")
        script_path = f.name

    try:
        # flush_tokens will force 'ucli% ' out immediately
        job = CmdJob("flush_test", f'python "{script_path}"', flush_tokens=("ucli% ",))
        with JobManager(max_workers=1) as manager:
            manager.add(job)
            
            t0 = time.time()
            found = False
            while time.time() - t0 < 1.0:
                # The line should be yielded well before the 2s sleep ends
                if any("ucli% " in line for line in job.logs()):
                    found = True
                    break
                time.sleep(0.1)
            
            assert found, "Flush token was not emitted immediately"
            job.cancel()
            manager.wait()
    finally:
        os.remove(script_path)

def test_cmd_job_fast_path_without_flush_tokens(monkeypatch):
    class FakeStdout:
        def __init__(self, lines):
            self._lines = iter(lines)

        def readline(self):
            return next(self._lines, "")

        def read(self, size=-1):
            raise AssertionError("fast path should not use read()")

    class FakeProc:
        def __init__(self):
            self.stdout = FakeStdout(["first line\n", "second line\n", "tail"])
            self.stdin = None
            self.returncode = 0
            self.pid = 1234

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    proc = FakeProc()
    monkeypatch.setattr("rpkbin.job_manager.cmd_job.subprocess.Popen", lambda *args, **kwargs: proc)

    job = CmdJob("fast_path", "fake command")
    job._status = RUNNING
    log_file = io.StringIO()

    job._execute(log_file=log_file)

    assert job.logs() == ["first line", "second line", "tail"]
    assert log_file.getvalue() == "first line\nsecond line\ntail"
    assert job.result == 0

def test_cmd_job_partial_eof_without_newline():
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write("import sys\nsys.stdout.write('tail-without-newline')\nsys.stdout.flush()\n")
        script_path = f.name

    try:
        job = CmdJob("partial_eof", f'python "{script_path}"')
        with JobManager(max_workers=1) as manager:
            manager.add(job)
            manager.wait()

        assert job.status == DONE
        assert job.logs()[-1] == "tail-without-newline"
    finally:
        os.remove(script_path)

def test_cmd_job_log_file_preserves_raw_output():
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(
            "import sys\n"
            "print('line one')\n"
            "print('line two')\n"
            "sys.stdout.write('tail')\n"
            "sys.stdout.flush()\n"
        )
        script_path = f.name

    try:
        job = CmdJob("log_file_raw", f'python "{script_path}"')
        job._status = RUNNING
        log_file = io.StringIO()

        job._execute(log_file=log_file)

        assert job.logs() == ["line one", "line two", "tail"]
        assert log_file.getvalue() == "line one\nline two\ntail"
    finally:
        os.remove(script_path)

def test_try_start_edge_case():
    ev = threading.Event()
    job1 = FuncJob("j1", lambda: ev.wait(timeout=2.0), resources={"gpu": 1})
    job2 = FuncJob("j2", lambda: 1, resources={"gpu": 1})
    
    with JobManager(max_workers=2, resources={"gpu": 1}) as manager:
        manager.add(job1)
        manager.add(job2)
        time.sleep(0.1)
        
        # job2 is pending because GPU is occupied
        assert job2.status == PENDING
        
        # Manually cancel job2 while it's pending in the queue
        job2.cancel()
        
        # Now job1 finishes, releasing GPU
        ev.set()
        
        # The manager will pick up job2, try to _manager_try_start(), 
        # which will return False because status is CANCELLED (not PENDING).
        # It should release the GPU and move on without throwing.
        manager.wait(timeout=2.0)
        
    assert job2.status == CANCELLED
    assert manager._used_resources.get("gpu", 0) == 0

def test_stop_waits_for_running_workers():
    ev = threading.Event()
    timer = threading.Timer(0.2, ev.set)
    job = FuncJob("slow_stop", lambda: ev.wait(timeout=1.0))

    manager = JobManager(max_workers=1)
    manager.start()
    manager.add(job)
    time.sleep(0.05)

    timer.start()
    t0 = time.monotonic()
    manager.stop()
    dt = time.monotonic() - t0

    timer.cancel()
    assert dt >= 0.15
    assert job.status == DONE

def test_wait_without_start_raises():
    manager = JobManager(max_workers=1)
    with pytest.raises(RuntimeError, match="JobManager is not running"):
        manager.wait(timeout=0.1)

def test_add_after_stop_raises():
    manager = JobManager(max_workers=1)
    manager.start()
    manager.stop()

    with pytest.raises(RuntimeError, match="already been stopped"):
        manager.add(FuncJob("late", lambda: None))

def test_slow_async_callback_warns(monkeypatch, caplog):
    times = iter([0.0, 1.2])
    monkeypatch.setattr(manager_mod.time, "perf_counter", lambda: next(times))
    caplog.set_level(logging.WARNING)

    called = []
    job = FuncJob("cb_slow", lambda: "ok")
    job.on_done(lambda j: called.append(j.name))

    with JobManager(max_workers=1) as manager:
        manager.add(job)
        manager.wait()

    assert called == ["cb_slow"]
    assert "Slow async callback detected" in caplog.text

def test_wait_inside_manager_callback_raises():
    errors = []
    callback_ran = threading.Event()

    manager = JobManager(max_workers=1)
    job = FuncJob("cb_wait", lambda: "ok")

    def on_done(j):
        try:
            manager.wait(timeout=0.1)
        except RuntimeError as exc:
            errors.append(str(exc))
        finally:
            callback_ran.set()

    job.on_done(on_done)

    with manager:
        manager.add(job)
        assert manager.wait(timeout=1.0)

    assert callback_ran.wait(timeout=1.0)
    assert errors
    assert "cannot be called from a manager callback" in errors[0]

def test_add_rejects_impossible_resource_request():
    job = FuncJob("need_gpu", lambda: "ok", resources={"gpu": 2})
    with JobManager(max_workers=1, resources={"gpu": 1}) as manager:
        with pytest.raises(ValueError, match="Impossible Resource Request"):
            manager.add(job)

def test_add_rejects_duplicate_job_id():
    jid = uuid.uuid4()
    job1 = FuncJob("a", lambda: 1, job_id=jid)
    job2 = FuncJob("b", lambda: 2, job_id=jid)
    with JobManager(max_workers=1) as manager:
        manager.add(job1)
        with pytest.raises(ValueError, match="already in the manager"):
            manager.add(job2)
        manager.wait()

def test_worker_setup_error_fails_job(monkeypatch):
    def boom_open(*args, **kwargs):
        raise OSError("log open boom")

    monkeypatch.setattr(manager_mod, "open", boom_open, raising=False)

    job = FuncJob("setup_error", lambda: "ok")
    td = f".tmp_sched_{uuid.uuid4().hex}"
    os.makedirs(td, exist_ok=True)
    try:
        with JobManager(max_workers=1, log_dir=td) as manager:
            manager.add(job)
            assert manager.wait(job.id, timeout=1.0)
    finally:
        shutil.rmtree(td, ignore_errors=True)

    assert job.status == FAILED
    assert job.error is not None


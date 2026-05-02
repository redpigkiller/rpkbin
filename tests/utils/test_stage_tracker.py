import threading
import time
import pytest
from unittest.mock import patch, MagicMock

from rpkbin.utils.stage_tracker import StageTracker, ErrorLevel, StageFailedError, UsageError, Issue

# ---------------------------------------------------------------------------
# Initialization & Mode Tests
# ---------------------------------------------------------------------------

def test_init_invalid_mode():
    with pytest.raises(ValueError, match="mode must be 'flat' or 'context'"):
        StageTracker(mode="invalid_mode")

def test_flat_mode_cannot_use_stage():
    with StageTracker(mode="flat") as t:
        with pytest.raises(UsageError, match=r"stage\(\) only valid in context mode"):
            with t.stage("Test"):
                pass

def test_context_mode_cannot_use_begin_stage():
    with StageTracker(mode="context") as t:
        with pytest.raises(UsageError, match=r"begin_stage\(\) only valid in flat mode"):
            t.begin_stage("Test")

# ---------------------------------------------------------------------------
# Flat Mode Tests
# ---------------------------------------------------------------------------

def test_flat_mode_normal_execution():
    with StageTracker(mode="flat") as t:
        t.begin_stage("Stage1")
        t.info("Process A")
        
        t.begin_stage("Stage2")
        t.info("Process B")
        t.warning("Some warning")

    issues = t.get_issues()
    # Warnings are tracked by default
    assert len(issues) == 1
    assert issues[0].level == ErrorLevel.WARNING
    assert issues[0].stage == "Stage2"

def test_flat_mode_accumulate_errors():
    with pytest.raises(StageFailedError) as exc_info:
        with StageTracker(mode="flat") as t:
            t.begin_stage("Validation")
            t.error("Invalid data point 1")
            t.error("Invalid data point 2")
            # Exiting `with` block triggers finalization and raises StageFailedError
            
    assert exc_info.value.stage == "Validation"
    assert exc_info.value.error_count == 2
    assert len(exc_info.value.issues) == 2

def test_flat_mode_fatal():
    with pytest.raises(StageFailedError) as exc_info:
        with StageTracker(mode="flat") as t:
            t.begin_stage("Init")
            t.fatal("System crash")
            
    assert exc_info.value.stage == "Init"
    assert exc_info.value.error_count == 1
    assert "System crash" in exc_info.value.issues[0].message

def test_flat_mode_checkpoint():
    with pytest.raises(StageFailedError):
        with StageTracker(mode="flat") as t:
            t.begin_stage("Load")
            t.error("Missing file")
            t.checkpoint() # Raises immediately
            t.info("This should not run")

# ---------------------------------------------------------------------------
# Context Mode Tests
# ---------------------------------------------------------------------------

def test_context_mode_normal_execution():
    with StageTracker(mode="context") as t:
        with t.stage("StageA"):
            t.info("Process A")
        
        with t.stage("StageB"):
            t.warning("Some warning")

    issues = t.get_issues()
    assert len(issues) == 1
    assert issues[0].level == ErrorLevel.WARNING
    assert issues[0].stage == "StageB"

def test_context_mode_accumulate_errors():
    with pytest.raises(StageFailedError) as exc_info:
        with StageTracker(mode="context") as t:
            with t.stage("Process"):
                t.error("Error 1")
                t.error("Error 2")
            # Exiting the `with t.stage` raises health check exception
            
    assert exc_info.value.stage == "Process"
    assert exc_info.value.error_count == 2

def test_context_mode_exception_reraise():
    """Test that a non-StageFailedError exception inside a stage is propagated."""
    with pytest.raises(ValueError, match="Custom error"):
        with StageTracker(mode="context") as t:
            with t.stage("Math"):
                raise ValueError("Custom error")

def test_context_mode_fatal():
    with pytest.raises(StageFailedError) as exc_info:
        with StageTracker(mode="context") as t:
            with t.stage("Init"):
                t.fatal("Memory full")
    
    assert exc_info.value.stage == "Init"

def test_context_mode_nested_not_allowed():
    with StageTracker(mode="context") as t:
        with pytest.raises(UsageError, match="Nested stages are not supported"):
            with t.stage("Outer"):
                with t.stage("Inner"):
                    pass

def test_duplicate_stage_name():
    with StageTracker(mode="context") as t:
        with t.stage("Same"):
            pass
        with pytest.raises(UsageError, match="already exists"):
            with t.stage("Same"):
                pass

# ---------------------------------------------------------------------------
# Logging & Issue Querying
# ---------------------------------------------------------------------------

def test_issue_querying():
    t = StageTracker(mode="flat")
    t._entered = True # Bypass entered check for explicit testing
    t.begin_stage("A")
    t.debug("d1", track=True)
    t.info("i1", track=True)
    try:
        t.begin_stage("B")
    except StageFailedError:
        pass # Expected because A had an error
        
    # Manually set Stage B instead, or simply bypass health check for this test
    # Actually if B failed, current stage is None.
    # We should catch it, but `t.error("e2")` will go to "System" or None.
    # Let's bypass health check logic by not entering B with begin_stage, or just clear issues.
    
    t.current_stage = None
    t.clear_issues()
    t.begin_stage("A")
    t.debug("d1", track=True)
    t.info("i1", track=True)
    t.warning("w1", track=True)
    t.error("e1")
    
    # Inject directly
    with t._issues_lock:
        t._issues["B"] = [Issue(ErrorLevel.ERROR, "e2", "B")]
    
    # get all
    assert len(t.get_issues()) == 5
    
    # get by stage
    assert len(t.get_issues(stage="A")) == 4
    
    # get by level
    assert len(t.get_issues(level=ErrorLevel.ERROR)) == 2
    assert len(t.get_issues(level="error")) == 2
    
    # get multiple levels
    assert len(t.get_issues(level=["error", "warning"])) == 3
    
    # get by stage and level
    assert len(t.get_issues(stage="A", level="error")) == 1
    
    t.current_stage = None
    t.clear_issues()

# ---------------------------------------------------------------------------
# Thread Safety
# ---------------------------------------------------------------------------

def thread_worker(tracker, thread_name, raises=False):
    t_name = threading.current_thread().name
    
    # Flat mode is thread-local, each thread should have its own stages
    tracker.begin_stage("LocalInit")
    tracker.warning(f"Warning from {t_name}")
    
    tracker.begin_stage("LocalWork")
    if raises:
        tracker.error(f"Error from {t_name}")
    
    if raises:
        try:
            tracker.checkpoint()
        except StageFailedError:
            pass
    else:
        tracker.checkpoint()

def test_threading_isolation():
    tracker = StageTracker(mode="flat")
    tracker._entered = True # Bypass check for multi-thread testing
    
    t1 = threading.Thread(target=thread_worker, args=(tracker, "T1", False), name="T1")
    t2 = threading.Thread(target=thread_worker, args=(tracker, "T2", True), name="T2")
    
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    issues = tracker.get_issues()
    assert len(issues) == 3 # 1 warning T1, 1 warning T2, 1 error T2
    
    # Check threads stage names
    assert len(tracker.get_issues(stage="LocalInit#T1")) == 1
    assert len(tracker.get_issues(stage="LocalInit#T2")) == 1
    assert len(tracker.get_issues(stage="LocalWork#T2")) == 1

# ---------------------------------------------------------------------------
# Summary Output
# ---------------------------------------------------------------------------

@patch('builtins.print')
def test_plain_summary_output(mock_print):
    # Test plain text fallback
    with pytest.raises(StageFailedError):
        with StageTracker(mode="flat", plain=True) as t:
            t.begin_stage("Process")
            t.warning("Warn")
            t.error("Err")
    
    # mock_print should have been called for summary
    assert mock_print.called
    
    # Check if SUCCESS/FAILED appears
    output = "\n".join([call.args[0] for call in mock_print.call_args_list if type(call.args[0]) is str])
    assert "FAILED: 1 critical/errors found" in output
    assert "Process" in output

def test_successful_summary():
    with StageTracker(mode="flat", plain=True) as t:
        t.begin_stage("A")
        t.info("Ok")
    
    assert t.summary(raise_errors=True) == True

import threading
import pytest
from unittest.mock import patch

from rpkbin.utils.stage_tracker import StageTracker, ErrorLevel, StageFailedError, UsageError

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

def test_flat_stage_failure_clears_current_stage():
    tracker = StageTracker(mode="flat")
    with pytest.raises(StageFailedError):
        with tracker as t:
            t.begin_stage("A")
            t.error("bad")
            with pytest.raises(StageFailedError):
                t.begin_stage("B")
            assert t.current_stage is None

    assert tracker.current_stage is None

def test_workflow_operations_rejected_after_exit():
    with StageTracker(mode="flat") as t:
        pass

    for operation in (
        lambda: t.debug("debug"),
        lambda: t.info("info"),
        lambda: t.warning("warning"),
        lambda: t.error("error"),
        lambda: t.fatal("fatal"),
        lambda: t.begin_stage("stage"),
        t.checkpoint,
    ):
        with pytest.raises(UsageError):
            operation()

    with pytest.raises(UsageError):
        with t.stage("stage"):
            pass

def test_get_issues_is_readable_after_exit():
    with StageTracker(mode="flat") as t:
        t.warning("kept")

    assert [issue.message for issue in t.get_issues()] == ["kept"]

def test_general_exception_summary_is_failed_and_reraised(capsys):
    with pytest.raises(ValueError, match="boom"):
        with StageTracker(mode="flat", plain=True):
            raise ValueError("boom")

    output = capsys.readouterr().out
    assert "EXECUTION FAILED (ValueError)" in output
    assert "FAILED:" in output
    assert "SUCCESS" not in output

def test_summary_title_does_not_control_failure_state(capsys):
    with StageTracker(mode="flat", plain=True) as t:
        assert t.summary(
            title="EXECUTION FAILED (manual)",
            raise_errors=False,
        ) is True

    output = capsys.readouterr().out
    assert "SUCCESS" in output
    assert "FAILED: 0 critical/errors found." not in output

def test_system_error_fails_normal_exit():
    with pytest.raises(StageFailedError) as exc_info:
        with StageTracker(mode="flat", plain=True) as t:
            t.error("system failure")

    assert exc_info.value.stage == "System"

def test_summary_checks_accumulated_errors(capsys):
    with pytest.raises(StageFailedError):
        with StageTracker(mode="flat", plain=True) as t:
            t.begin_stage("A")
            t.error("bad")
            assert t.summary(raise_errors=False) is False

            with pytest.raises(StageFailedError) as exc_info:
                t.summary(raise_errors=True)
            assert exc_info.value.stage == "A"

    assert "FAILED:" in capsys.readouterr().out

def test_stage_failed_error_keeps_all_stage_issues():
    with pytest.raises(StageFailedError) as exc_info:
        with StageTracker(mode="flat", plain=True) as t:
            t.begin_stage("A")
            t.info("context", track=True)
            t.warning("warning")
            t.error("error")

    assert [issue.level for issue in exc_info.value.issues] == [
        ErrorLevel.INFO,
        ErrorLevel.WARNING,
        ErrorLevel.ERROR,
    ]
    assert exc_info.value.error_count == 1

def test_tracker_cannot_be_nested_or_reused():
    with StageTracker(mode="flat") as t:
        with pytest.raises(UsageError, match="already active"):
            with t:
                pass

    with pytest.raises(UsageError, match="cannot be reused"):
        with t:
            pass

# ---------------------------------------------------------------------------
# Logging & Issue Querying
# ---------------------------------------------------------------------------

def test_issue_querying():
    with pytest.raises(StageFailedError):
        with StageTracker(mode="flat") as t:
            t.begin_stage("A")
            t.debug("d1", track=True)
            t.info("i1", track=True)
            t.warning("w1", track=True)
            t.error("e1")
            t.summary(raise_errors=False)

            t.begin_stage("B")
            t.error("e2")

    assert len(t.get_issues()) == 5
    assert len(t.get_issues(stage="A")) == 4
    assert len(t.get_issues(level=ErrorLevel.ERROR)) == 2
    assert len(t.get_issues(level="error")) == 2
    assert len(t.get_issues(level=["error", "warning"])) == 3
    assert len(t.get_issues(stage="A", level="error")) == 1

# ---------------------------------------------------------------------------
# Thread Safety
# ---------------------------------------------------------------------------

def thread_worker(tracker, raises=False):
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
    with pytest.raises(StageFailedError):
        with StageTracker(mode="flat") as tracker:
            t1 = threading.Thread(target=thread_worker, args=(tracker, False), name="T1")
            t2 = threading.Thread(target=thread_worker, args=(tracker, True), name="T2")

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

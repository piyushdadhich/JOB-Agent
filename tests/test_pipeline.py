"""Unit tests for skills.role_evaluator.pipeline.

Mocks stage_2pre, stage_2c_score, stage_2c_counter, and the
Stage2a instance. No live Ollama, no requests to localhost.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from skills.role_evaluator.pipeline import (  # noqa: E402
    DECIDE_MODEL,
    FILTER_MODEL,
    PipelineVerdict,
    evaluate_posting,
    run_batch,
)
from skills.role_evaluator.stage2a import (  # noqa: E402
    Stage2aResult,
    Stage2aVerdict,
)
from skills.role_evaluator.stage2c_counter import (  # noqa: E402
    Stage2CCounterResult,
)
from skills.role_evaluator.stage2c_score import (  # noqa: E402
    Stage2CScoreResult,
)
from skills.role_evaluator.stage2pre import Stage2PreResult  # noqa: E402

PIPELINE = "skills.role_evaluator.pipeline"


# --- helpers ---------------------------------------------------------

def _stage2a_pass() -> MagicMock:
    s = MagicMock()
    s.evaluate.return_value = Stage2aVerdict(
        result=Stage2aResult.PASS_TO_2B,
        rule_fired="passed_all_rules",
        reasoning="ok",
    )
    return s


def _stage2a_reject(rule: str = "hard_exclusion") -> MagicMock:
    s = MagicMock()
    s.evaluate.return_value = Stage2aVerdict(
        result=Stage2aResult.REJECT_HARD,
        rule_fired=rule,
        reasoning="reject",
    )
    return s


def _pre_proceed() -> Stage2PreResult:
    return Stage2PreResult(
        verdict="PROCEED",
        skip_reason=None,
        raw_response="",
        latency_ms=100,
        prompt_eval_duration_ms=50,
        eval_duration_ms=50,
    )


def _pre_skip(reason: str = "OTHER") -> Stage2PreResult:
    return Stage2PreResult(
        verdict="SKIP",
        skip_reason=reason,
        raw_response="",
        latency_ms=100,
        prompt_eval_duration_ms=50,
        eval_duration_ms=50,
    )


def _score(
    fn: int = 2, dom: int = 2, sen: int = 2, disq: bool = False,
) -> Stage2CScoreResult:
    return Stage2CScoreResult(
        function_score=fn, function_evidence="x",
        domain_score=dom, domain_evidence="x",
        seniority_score=sen, seniority_evidence="x",
        disqualifier_present=disq,
        disqualifier_reason="x" if disq else None,
        raw_response="",
        latency_ms=200,
        prompt_eval_duration_ms=100,
        eval_duration_ms=100,
    )


def _counter(substantive: bool = False) -> Stage2CCounterResult:
    return Stage2CCounterResult(
        strongest_argument_against=(
            "x" * 50 if substantive else "no_substantive_counter"
        ),
        counter_is_substantive=substantive,
        raw_response="",
        latency_ms=80,
        prompt_eval_duration_ms=40,
        eval_duration_ms=40,
    )


def _posting(pid: int = 1) -> dict:
    return {
        "id": pid,
        "title": "Senior Project Manager",
        "employer": "Acme",
        "location": "Toronto, ON",
        "posting_text": "Lead delivery.",
    }


# --- evaluate_posting: routing ----------------------------------------

def test_skip_at_2a_no_llm_calls():
    s2a = _stage2a_reject()
    with patch(f"{PIPELINE}.stage_2pre") as mp, \
         patch(f"{PIPELINE}.stage_2c_score") as ms, \
         patch(f"{PIPELINE}.stage_2c_counter") as mc:
        result = evaluate_posting(
            _posting(), "INV", None, s2a, MagicMock(), MagicMock(),
        )
    assert result.tier == "SKIP"
    assert result.skip_at == "2a"
    mp.assert_not_called()
    ms.assert_not_called()
    mc.assert_not_called()


def test_skip_at_2a_records_rule_fired():
    s2a = _stage2a_reject(rule="seniority_floor")
    with patch(f"{PIPELINE}.stage_2pre"), \
         patch(f"{PIPELINE}.stage_2c_score"), \
         patch(f"{PIPELINE}.stage_2c_counter"):
        result = evaluate_posting(
            _posting(), "INV", None, s2a, MagicMock(), MagicMock(),
        )
    assert result.skip_reason == "seniority_floor"


def test_skip_at_2pre_no_decide_calls():
    s2a = _stage2a_pass()
    with patch(f"{PIPELINE}.stage_2pre",
               return_value=_pre_skip("NO_FUNCTIONAL_OVERLAP")) as mp, \
         patch(f"{PIPELINE}.stage_2c_score") as ms, \
         patch(f"{PIPELINE}.stage_2c_counter") as mc:
        result = evaluate_posting(
            _posting(), "INV", None, s2a, MagicMock(), MagicMock(),
        )
    assert result.tier == "SKIP"
    assert result.skip_at == "2pre"
    assert result.skip_reason == "NO_FUNCTIONAL_OVERLAP"
    assert result.pre_result is not None
    mp.assert_called_once()
    ms.assert_not_called()
    mc.assert_not_called()


def test_proceed_runs_score_stage():
    s2a = _stage2a_pass()
    with patch(f"{PIPELINE}.stage_2pre", return_value=_pre_proceed()), \
         patch(f"{PIPELINE}.stage_2c_score",
               return_value=_score(1, 1, 1)) as ms, \
         patch(f"{PIPELINE}.stage_2c_counter") as mc:
        result = evaluate_posting(
            _posting(), "INV", None, s2a, MagicMock(), MagicMock(),
        )
    ms.assert_called_once()
    # 1*3 + 1*2 + 1*2 = 7 / 21 = 33.3% -> SKIP, no counter
    assert result.tier == "SKIP"
    mc.assert_not_called()


# --- evaluate_posting: counter conditional ----------------------------

def test_counter_runs_only_for_strong_and_top_tier():
    s2a = _stage2a_pass()
    # 2*3 + 2*2 + 2*2 = 14 / 21 = 66.7% -> STRONG
    with patch(f"{PIPELINE}.stage_2pre", return_value=_pre_proceed()), \
         patch(f"{PIPELINE}.stage_2c_score", return_value=_score(2, 2, 2)), \
         patch(f"{PIPELINE}.stage_2c_counter",
               return_value=_counter(False)) as mc:
        evaluate_posting(_posting(), "INV", None, s2a,
                         MagicMock(), MagicMock())
    mc.assert_called_once()


def test_counter_runs_for_top_tier():
    s2a = _stage2a_pass()
    with patch(f"{PIPELINE}.stage_2pre", return_value=_pre_proceed()), \
         patch(f"{PIPELINE}.stage_2c_score", return_value=_score(3, 3, 3)), \
         patch(f"{PIPELINE}.stage_2c_counter",
               return_value=_counter(False)) as mc:
        evaluate_posting(_posting(), "INV", None, s2a,
                         MagicMock(), MagicMock())
    mc.assert_called_once()


def test_counter_skipped_for_exploratory():
    s2a = _stage2a_pass()
    # 2*3 + 1*2 + 1*2 = 10 / 21 = 47.6% -> EXPLORATORY
    with patch(f"{PIPELINE}.stage_2pre", return_value=_pre_proceed()), \
         patch(f"{PIPELINE}.stage_2c_score", return_value=_score(2, 1, 1)), \
         patch(f"{PIPELINE}.stage_2c_counter") as mc:
        result = evaluate_posting(_posting(), "INV", None, s2a,
                                  MagicMock(), MagicMock())
    mc.assert_not_called()
    assert result.tier == "EXPLORATORY"


def test_counter_skipped_for_skip():
    s2a = _stage2a_pass()
    with patch(f"{PIPELINE}.stage_2pre", return_value=_pre_proceed()), \
         patch(f"{PIPELINE}.stage_2c_score", return_value=_score(0, 0, 0)), \
         patch(f"{PIPELINE}.stage_2c_counter") as mc:
        result = evaluate_posting(_posting(), "INV", None, s2a,
                                  MagicMock(), MagicMock())
    mc.assert_not_called()
    assert result.tier == "SKIP"


# --- evaluate_posting: client wiring ----------------------------------

def test_decide_uses_e4b_client():
    s2a = _stage2a_pass()
    llm_filter = MagicMock(name="filter")
    llm_decide = MagicMock(name="decide")
    with patch(f"{PIPELINE}.stage_2pre", return_value=_pre_proceed()), \
         patch(f"{PIPELINE}.stage_2c_score",
               return_value=_score(2, 2, 2)) as ms, \
         patch(f"{PIPELINE}.stage_2c_counter",
               return_value=_counter(False)) as mc:
        evaluate_posting(_posting(), "INV", None, s2a,
                         llm_filter, llm_decide)
    args_score, _ = ms.call_args
    assert args_score[3] is llm_decide
    args_counter, _ = mc.call_args
    assert args_counter[3] is llm_decide


def test_filter_uses_gemma3_client():
    s2a = _stage2a_pass()
    llm_filter = MagicMock(name="filter")
    llm_decide = MagicMock(name="decide")
    with patch(f"{PIPELINE}.stage_2pre",
               return_value=_pre_proceed()) as mp, \
         patch(f"{PIPELINE}.stage_2c_score", return_value=_score()), \
         patch(f"{PIPELINE}.stage_2c_counter", return_value=_counter(False)):
        evaluate_posting(_posting(), "INV", None, s2a,
                         llm_filter, llm_decide)
    args_pre, _ = mp.call_args
    assert args_pre[3] is llm_filter


# --- evaluate_posting: result shape -----------------------------------

def test_full_pipeline_returns_tier():
    s2a = _stage2a_pass()
    with patch(f"{PIPELINE}.stage_2pre", return_value=_pre_proceed()), \
         patch(f"{PIPELINE}.stage_2c_score", return_value=_score(3, 3, 3)), \
         patch(f"{PIPELINE}.stage_2c_counter", return_value=_counter(False)):
        result = evaluate_posting(
            _posting(), "INV", None, s2a, MagicMock(), MagicMock(),
        )
    assert isinstance(result, PipelineVerdict)
    assert result.tier in ("TOP_TIER", "STRONG", "EXPLORATORY", "SKIP")


def test_latency_breakdown_recorded():
    s2a = _stage2a_pass()
    with patch(f"{PIPELINE}.stage_2pre", return_value=_pre_proceed()), \
         patch(f"{PIPELINE}.stage_2c_score", return_value=_score(2, 2, 2)), \
         patch(f"{PIPELINE}.stage_2c_counter", return_value=_counter(False)):
        result = evaluate_posting(
            _posting(), "INV", None, s2a, MagicMock(), MagicMock(),
        )
    bd = result.latency_breakdown
    assert "2a_ms" in bd
    assert "2pre_ms" in bd
    assert "2c_score_ms" in bd
    assert "2c_counter_ms" in bd
    assert bd["2pre_ms"] == 100  # from _pre_proceed
    assert bd["2c_score_ms"] == 200  # from _score()
    assert bd["2c_counter_ms"] == 80  # from _counter()


def test_components_in_verdict():
    s2a = _stage2a_pass()
    with patch(f"{PIPELINE}.stage_2pre", return_value=_pre_proceed()), \
         patch(f"{PIPELINE}.stage_2c_score",
               return_value=_score(2, 1, 3, disq=True)), \
         patch(f"{PIPELINE}.stage_2c_counter", return_value=_counter(False)):
        result = evaluate_posting(
            _posting(), "INV", None, s2a, MagicMock(), MagicMock(),
        )
    assert result.combined is not None
    comps = result.combined.components
    assert comps["function"] == 2
    assert comps["domain"] == 1
    assert comps["seniority"] == 3
    assert comps["disqualifier"] is True


def test_pre_result_recorded_when_proceed():
    s2a = _stage2a_pass()
    pre = _pre_proceed()
    with patch(f"{PIPELINE}.stage_2pre", return_value=pre), \
         patch(f"{PIPELINE}.stage_2c_score", return_value=_score(2, 2, 2)), \
         patch(f"{PIPELINE}.stage_2c_counter", return_value=_counter(False)):
        result = evaluate_posting(
            _posting(), "INV", None, s2a, MagicMock(), MagicMock(),
        )
    assert result.pre_result is pre


# --- run_batch: two-phase ordering ------------------------------------

def test_batch_filter_phase_before_decide_phase():
    """All stage_2pre calls in run_batch must complete before any
    stage_2c_score call begins. Otherwise the model swap math is
    wrong (we'd be loading both filter and decide concurrently)."""
    s2a = _stage2a_pass()
    call_order: list = []

    def pre_side(*a, **kw):
        call_order.append(("pre", a[0]["id"]))
        return _pre_proceed()

    def score_side(*a, **kw):
        call_order.append(("score", a[0]["id"]))
        return _score(2, 2, 2)

    with patch(f"{PIPELINE}.stage_2pre", side_effect=pre_side), \
         patch(f"{PIPELINE}.stage_2c_score", side_effect=score_side), \
         patch(f"{PIPELINE}.stage_2c_counter",
               return_value=_counter(False)), \
         patch(f"{PIPELINE}._unload_model"):
        postings = [_posting(pid=i) for i in range(1, 4)]
        run_batch(postings, "INV", None, s2a, MagicMock(), MagicMock())

    pre_indices = [i for i, (k, _) in enumerate(call_order) if k == "pre"]
    score_indices = [i for i, (k, _) in enumerate(call_order) if k == "score"]
    assert pre_indices and score_indices
    assert max(pre_indices) < min(score_indices)


def test_batch_unloads_filter_between_phases():
    """Verify _unload_model is called between phase 1 and phase 2."""
    s2a = _stage2a_pass()
    with patch(f"{PIPELINE}.stage_2pre", return_value=_pre_proceed()), \
         patch(f"{PIPELINE}.stage_2c_score", return_value=_score()), \
         patch(f"{PIPELINE}.stage_2c_counter", return_value=_counter(False)), \
         patch(f"{PIPELINE}._unload_model") as mu:
        run_batch(
            [_posting(pid=1)], "INV", None, s2a,
            MagicMock(), MagicMock(),
        )
    mu.assert_called_once()
    args, _ = mu.call_args
    # First positional is host string; second is model name
    assert args[1] == FILTER_MODEL


def test_batch_decide_exception_recorded_as_exploratory():
    """If the decide stage raises, the verdict is recorded as
    EXPLORATORY with the exception info in skip_reason — the run
    must not crash."""
    s2a = _stage2a_pass()
    with patch(f"{PIPELINE}.stage_2pre", return_value=_pre_proceed()), \
         patch(f"{PIPELINE}.stage_2c_score",
               side_effect=RuntimeError("boom")), \
         patch(f"{PIPELINE}.stage_2c_counter"), \
         patch(f"{PIPELINE}._unload_model"):
        verdicts = run_batch(
            [_posting(pid=42)], "INV", None, s2a,
            MagicMock(), MagicMock(),
        )
    v = verdicts[42]
    assert v.tier == "EXPLORATORY"
    assert v.skip_reason is not None
    assert "decide_exception" in v.skip_reason


def test_batch_returns_dict_keyed_by_posting_id():
    s2a = _stage2a_pass()
    with patch(f"{PIPELINE}.stage_2pre", return_value=_pre_proceed()), \
         patch(f"{PIPELINE}.stage_2c_score", return_value=_score(2, 2, 2)), \
         patch(f"{PIPELINE}.stage_2c_counter", return_value=_counter(False)), \
         patch(f"{PIPELINE}._unload_model"):
        verdicts = run_batch(
            [_posting(pid=10), _posting(pid=20), _posting(pid=30)],
            "INV", None, s2a, MagicMock(), MagicMock(),
        )
    assert set(verdicts.keys()) == {10, 20, 30}
    for v in verdicts.values():
        assert isinstance(v, PipelineVerdict)

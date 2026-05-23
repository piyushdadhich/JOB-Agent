"""Role evaluator skill: Stage 2a deterministic filter and the v2.3
pipeline (Stage 2pre filter + Stage 2c decomposed scoring +
Stage 2c factored counter + deterministic score combine).

(Legacy Stage 2b monolithic evaluator + RoleEvaluator removed in
Spec 4 TASK 1. FewShotSelector is preserved for live test coverage
in tests/test_flag_feedback.py and any future Stage-2b-style
consumer.)
"""
from .few_shot import FewShotExample, FewShotSelector
from .profile_config import ProfileConfig
from .score import CombinedScore, combine
from .stage2a import Stage2a, Stage2aResult, Stage2aVerdict
from .stage2c_counter import (
    Stage2CCounterResult,
    is_substantive,
    stage_2c_counter,
)
from .stage2c_score import (
    Stage2CScoreError,
    Stage2CScoreResult,
    stage_2c_score,
)
from .stage2pre import Stage2PreResult, stage_2pre

__all__ = [
    "CombinedScore",
    "FewShotExample",
    "FewShotSelector",
    "ProfileConfig",
    "Stage2a",
    "Stage2aResult",
    "Stage2aVerdict",
    "Stage2CCounterResult",
    "Stage2CScoreError",
    "Stage2CScoreResult",
    "Stage2PreResult",
    "combine",
    "is_substantive",
    "stage_2c_counter",
    "stage_2c_score",
    "stage_2pre",
]

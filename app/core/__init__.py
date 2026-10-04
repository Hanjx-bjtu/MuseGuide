"""契约层：全项目共享的数据形状。

**零业务依赖铁律：** 本包（及其子模块）不得 import ``services`` / ``providers`` /
``fastapi`` / ``streamlit``。该规则由 ``tests/test_architecture.py`` 静态强制。

依据：``MVP计划.md`` §3.3.3 / §3.4.1 / §3.4.2 / §3.5.2 / §3.6.2 / §3.9.3
详见：``docs/DATA_CONTRACTS.md``
"""

from app.core.artifact import ChordSymbol, MelodyInfo, MusicArtifact, Section
from app.core.brief import (
    CreationInput,
    CreativeGoal,
    CreativeIntent,
    SelectionInput,
    UserLevel,
)
from app.core.config import Settings, get_settings
from app.core.degradation import Degradation, DegradationKind
from app.core.evidence import (
    Candidate,
    DecomposedQuery,
    Evidence,
    EvidenceSource,
    GroundingIssue,
    GroundingReport,
    IssueKind,
)
from app.core.options import (
    EMOTION_OPTIONS,
    STYLE_OPTIONS,
    TEMPO_OPTIONS,
    Option,
    emotion_concepts,
    emotion_option,
    find_option,
    options_as_payload,
    selection_payload,
    style_concepts,
    style_option,
    tempo_bpm,
    tempo_option_by_label,
)
from app.core.plan import (
    Advice,
    AdviceOption,
    AnalysisResult,
    LaymanNote,
    StarterPlan,
    StarterSection,
)
from app.core.trace import Trace

__all__ = [
    # artifact
    "ChordSymbol",
    "MelodyInfo",
    "MusicArtifact",
    "Section",
    # brief
    "CreationInput",
    "CreativeGoal",
    "CreativeIntent",
    "SelectionInput",
    "UserLevel",
    # config / infra
    "Settings",
    "get_settings",
    "Degradation",
    "DegradationKind",
    # evidence
    "Candidate",
    "DecomposedQuery",
    "Evidence",
    "EvidenceSource",
    "GroundingIssue",
    "GroundingReport",
    "IssueKind",
    # options
    "EMOTION_OPTIONS",
    "STYLE_OPTIONS",
    "TEMPO_OPTIONS",
    "Option",
    "emotion_concepts",
    "emotion_option",
    "find_option",
    "options_as_payload",
    "selection_payload",
    "style_concepts",
    "style_option",
    "tempo_bpm",
    "tempo_option_by_label",
    # plan
    "Advice",
    "AdviceOption",
    "AnalysisResult",
    "LaymanNote",
    "StarterPlan",
    "StarterSection",
    # trace
    "Trace",
]

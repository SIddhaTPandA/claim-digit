"""
src/config package
==================
Re-exports every public symbol from the individual config modules so that
callers can import from a single location:

    from src.config import (
        AGENT_ROLE, AGENT_GOAL, AGENT_BACKSTORY,
        SYSTEM_CONTEXT, FEW_SHOT_EXAMPLES, build_task_description,
        TASK_EXPECTED_OUTPUT,
    )

Each module owns one concern:
  backstory.py       — agent role, goal, backstory strings
  goal.py            — standalone AGENT_GOAL constant (mirrors backstory.py)
  task.py            — extraction prompt: SYSTEM_CONTEXT, FEW_SHOT_EXAMPLES,
                       build_task_description()
  expected_output.py — TASK_EXPECTED_OUTPUT string for the CrewAI Task
"""

from src.config.backstory import AGENT_ROLE, AGENT_GOAL, AGENT_BACKSTORY
from src.config.goal import AGENT_GOAL  # noqa: F811  (same value, kept for direct import)
from src.config.task import SYSTEM_CONTEXT, FEW_SHOT_EXAMPLES, build_task_description
from src.config.expected_output import TASK_EXPECTED_OUTPUT

__all__ = [
    # Agent identity
    "AGENT_ROLE",
    "AGENT_GOAL",
    "AGENT_BACKSTORY",
    # Task prompt
    "SYSTEM_CONTEXT",
    "FEW_SHOT_EXAMPLES",
    "build_task_description",
    # Task expected output
    "TASK_EXPECTED_OUTPUT",
]

"""LLM fact extraction with code-verified source spans (M1-06).

The model proposes facts with the patient's words; code keeps only those whose quote it finds in a
patient turn, and computes every offset itself.
"""

from careflow_intake.extraction.extractor import LlmExtractor
from careflow_intake.extraction.prompt import PROMPT_VERSION

__all__ = ["PROMPT_VERSION", "LlmExtractor"]

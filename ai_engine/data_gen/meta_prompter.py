"""Meta-prompting: generate diversity rules from a task description.

Once per SDG job, we ask a cheap LLM to produce a list of stylistic
"rules" that subsequent Generator calls cycle through. Drives the
coverage_pool that broadens topical / phrasing / structure variety.

Output schema:
  {
    "diversity_rules":          ["...", ...]   # >=8 items
    "unknown_diversity_rules":  ["...", ...]   # >=5 items, only used for
                                                  classification + tool_calling
                                                  sentinel rows; absent for QA.
  }

Failure mode: if the LLM response can't be parsed into the schema, we
fall back to a hardcoded generic-but-decent rule set so the SDG job can
still proceed. The fallback is logged.
"""

from __future__ import annotations

import logging
from json import JSONDecodeError

from pydantic import BaseModel, ConfigDict, Field, ValidationError

log = logging.getLogger(__name__)


class SDGRules(BaseModel):
    """Output of the meta-prompter."""

    model_config = ConfigDict(extra="ignore")

    diversity_rules: list[str] = Field(..., min_length=8)
    # Optional. Generators of the QA pipeline have no sentinel quota and
    # therefore no "unknown" rule pool.
    unknown_diversity_rules: list[str] = Field(default_factory=list)


# ---- Hardcoded fallback rules -------------------------------------------


_GENERIC_DIVERSITY_RULES: list[str] = [
    "Vary the topic concretely — pick different domains, not just rephrasings of one example.",
    "Mix short, terse phrasings with longer multi-sentence prompts.",
    "Include questions / statements that contain a typo, an abbreviation, or informal slang.",
    "Vary the user's apparent expertise: mix novice phrasing with expert / technical phrasing.",
    "Include polite, neutral, and direct register variations.",
    "Vary specificity: some prompts highly specific (named entities, numbers), some open-ended.",
    "Include first-person, second-person, and impersonal phrasings.",
    "Add edge cases: empty values, ambiguous referents, multi-step requests.",
    "Mix question forms: yes/no, wh-, imperative, multi-clause.",
    "Vary length distribution — most short, a few long, occasional very-long.",
]

_GENERIC_UNKNOWN_RULES: list[str] = [
    "Off-topic chit-chat that has nothing to do with the task domain.",
    "Ambiguous request where the intent could match several tools / labels — pick none clearly.",
    "Request that hints at a capability outside the task scope (translation, math, etc.).",
    "Greeting / farewell / acknowledgement-only messages.",
    "Highly truncated or garbled input that can't be confidently classified.",
    "Multi-intent prompt where one of the intents is in-scope but ambiguity wins.",
]


def fallback_rules(*, include_unknown: bool) -> SDGRules:
    """Return the hardcoded generic rules. Used when the LLM call fails."""
    return SDGRules(
        diversity_rules=list(_GENERIC_DIVERSITY_RULES),
        unknown_diversity_rules=(
            list(_GENERIC_UNKNOWN_RULES) if include_unknown else []
        ),
    )


def parse_meta_response(raw: str, *, include_unknown: bool) -> SDGRules:
    """Parse one meta-prompter response. Falls back to generic rules on any error.

    Args:
        raw: the LLM's JSON response.
        include_unknown: True for classification + tool_calling (which need
            sentinel rules); False for QA.
    """
    if not raw or not raw.strip():
        log.warning("meta-prompter returned empty response; using fallback rules")
        return fallback_rules(include_unknown=include_unknown)
    try:
        rules = SDGRules.model_validate_json(raw.strip())
    except (ValidationError, JSONDecodeError, ValueError) as exc:
        log.warning(
            "meta-prompter response did not parse (%s); using fallback rules",
            type(exc).__name__,
        )
        return fallback_rules(include_unknown=include_unknown)
    if include_unknown and len(rules.unknown_diversity_rules) < 5:
        # Pad with generic rules to satisfy the contract — same intent as
        # the old script's fallback path.
        rules = SDGRules(
            diversity_rules=rules.diversity_rules,
            unknown_diversity_rules=(
                rules.unknown_diversity_rules + _GENERIC_UNKNOWN_RULES
            )[: max(5, len(rules.unknown_diversity_rules))]
            if rules.unknown_diversity_rules
            else list(_GENERIC_UNKNOWN_RULES),
        )
    return rules


__all__ = ["SDGRules", "fallback_rules", "parse_meta_response"]

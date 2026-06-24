"""Agentic tools used to augment LLM grading.

verify_math(text) -- extracts arithmetic expressions and computes correctness
                     with sympy. Catches calculation errors the LLM misses.
"""
from __future__ import annotations

import re
from typing import Any
import sympy as sp


# ───────────────────────────────────────────────────────────────────────
# Math verifier
# ───────────────────────────────────────────────────────────────────────
# Find expressions like "2 + 3 = 5" or "(4 * 6) / 2 = 12"
_EQ_PATTERN = re.compile(
    r"""
    (?<![A-Za-z_\d])             # left boundary (not letter/digit)
    (                            # LHS (capture group 1)
      [\d\.\s\+\-\*\/\(\)]+      # numbers, basic ops, parens
    )
    \s*=\s*
    (                            # RHS (capture group 2)
      -?\d+(?:\.\d+)?            # a single number (possibly negative or decimal)
    )
    (?![A-Za-z_\d])              # right boundary
    """,
    re.VERBOSE,
)


def _safe_eval(expr: str) -> float | None:
    """Evaluate a numeric expression with sympy. Returns None on failure."""
    try:
        # sympify is safer than eval — only math constants/operators
        cleaned = expr.replace(" ", "")
        if not cleaned: return None
        result = sp.sympify(cleaned, evaluate=True)
        return float(result)
    except Exception:
        return None


def verify_math(text: str, tol: float = 1e-4) -> dict[str, Any]:
    """Extract arithmetic expressions from `text` and check each one.

    Returns:
        {
          "expressions_found": int,
          "errors":   [{"expression": str, "claimed": float, "correct": float}],
          "passed":   int,   # count of correct equalities
          "verified": bool,  # True if no errors found
        }
    """
    if not text or not isinstance(text, str):
        return {"expressions_found": 0, "errors": [], "passed": 0, "verified": True}

    found = []
    errors = []
    passed = 0
    for m in _EQ_PATTERN.finditer(text):
        lhs, rhs = m.group(1).strip(), m.group(2).strip()
        if not lhs or not rhs: continue
        # Skip if LHS is just a single number (not an equation worth checking)
        if re.fullmatch(r"-?\d+(?:\.\d+)?", lhs.replace(" ", "")):
            continue
        lhs_val = _safe_eval(lhs)
        rhs_val = _safe_eval(rhs)
        if lhs_val is None or rhs_val is None:
            continue
        found.append(f"{lhs} = {rhs}")
        if abs(lhs_val - rhs_val) > tol:
            errors.append({
                "expression": f"{lhs} = {rhs}",
                "claimed":  rhs_val,
                "correct":  round(lhs_val, 4),
            })
        else:
            passed += 1
    return {
        "expressions_found": len(found),
        "errors":   errors,
        "passed":   passed,
        "verified": len(errors) == 0,
    }

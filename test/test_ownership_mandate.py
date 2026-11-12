"""The ownership mandate stays one text across every reviewer copy.

Authors read it from the ``## Own the codebase`` section of the root
``AGENTS.md``. Reviewers read a reviewer-facing copy inlined in every AI review
lane that judges code structure, so an edit to one lane's copy that misses the
others would hand the lanes different contracts.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

AUTHOR_SOURCE = ROOT / "AGENTS.md"
AUTHOR_FIRST = "This codebase is yours:"
AUTHOR_LAST = "or an explicit stop from the user."

REVIEW_FIRST = "OWN THE CODEBASE, IN BOTH DIRECTIONS."
REVIEW_LAST = "The security invariants in AGENTS.md still bind."
REVIEW_SOURCES = (
    ROOT / ".github" / "review-prompts" / "first-principles.md",
    ROOT / ".github" / "review-prompts" / "gpt-repo-context.md",
    ROOT / ".github" / "review-prompts" / "opus-discovery.md",
    ROOT / ".github" / "review-prompts" / "opus-validate.md",
    ROOT / ".github" / "review-prompts" / "security-scope.md",
    ROOT / ".github" / "workflows" / "fork-gpt-review.yml",
)


def _block(path: Path, first: str, last: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if first in line]
    assert len(starts) == 1, f"{path.relative_to(ROOT)} carries {len(starts)} copies of {first!r}"
    start = starts[0]
    end = next((i for i in range(start, len(lines)) if lines[i].rstrip().endswith(last)), None)
    assert end is not None, f"{path.relative_to(ROOT)}: {first!r} block never reaches {last!r}"
    block = lines[start : end + 1]
    indent = len(block[0]) - len(block[0].lstrip())
    return "\n".join(line[indent:] if line.strip() else "" for line in block)


def test_every_reviewer_copy_is_byte_identical():
    reference = _block(REVIEW_SOURCES[0], REVIEW_FIRST, REVIEW_LAST)
    for path in REVIEW_SOURCES[1:]:
        assert _block(path, REVIEW_FIRST, REVIEW_LAST) == reference, (
            f"{path.relative_to(ROOT)} drifted from {REVIEW_SOURCES[0].relative_to(ROOT)}; "
            "edit every reviewer copy of the ownership mandate together"
        )


def test_both_copies_keep_the_security_carve_out():
    assert "security invariants" in _block(AUTHOR_SOURCE, AUTHOR_FIRST, AUTHOR_LAST)
    assert "widening a security fence" in _block(REVIEW_SOURCES[0], REVIEW_FIRST, REVIEW_LAST)


def test_the_reviewer_copy_leaves_each_lane_its_own_bar():
    block = _block(REVIEW_SOURCES[0], REVIEW_FIRST, REVIEW_LAST)
    assert "only under this review's own bar" in block
    assert "Never report" not in block


def test_the_design_review_prompts_do_not_inline_the_block():
    # Each prompt is one expression-bearing string; GitHub silently rejects the
    # workflow once it passes 21000 characters, and the block takes both past it.
    for name in ("design-review.yml", "fork-design-review.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert REVIEW_FIRST not in text, name

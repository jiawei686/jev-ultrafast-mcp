"""The handshake text decides what a host does by default.

`instructions` is the one piece of prose a client receives before it picks a
tool, so it is the server's only chance to say how it wants to be used -- and
for a long time it said the wrong thing. It described the manual loop
(`browser_open -> observe -> act -> assert`) and never mentioned `browser_goal`,
even though handing the task over is the entire point of the server. The
README, `llms.txt` and `browser_goal`'s own docstring all say so; the runtime
instructions were the one place that disagreed, and the runtime instructions are
the ones a host actually reads.

The result was predictable: a host did what it was told and drove the loop
itself, one call per click, and delegation only happened when a human asked for
it by name.

So the property worth pinning is not "the word appears somewhere" -- a guard
that weak passes while the text still reads as "drive it yourself". It is that
the handoff is stated **first**, that it is stated as the *rule* for a browser
task rather than an option, and that the manual loop is marked as the fallback
rather than presented as *the* loop.

The one line the rule must not cross is reading. A read is deterministic, free
and needs no key; handing one to a decision model spends money to answer a
question the element table already answers. So "hand the task over" has to
arrive with "a look is not a task", and both halves are pinned below.
"""

from jev_ultrafast_mcp import server as S

INSTRUCTIONS = S.INSTRUCTIONS


def test_the_handoff_is_offered_before_the_manual_loop():
    """Order is the point: the first instruction shapes the default behaviour."""
    handoff = INSTRUCTIONS.find("browser_goal")
    manual = INSTRUCTIONS.find("browser_open")

    assert handoff != -1, (
        "the instructions never mention browser_goal, so a host is never told it "
        "can hand the task over")
    assert manual != -1, (
        "the manual loop should still be documented -- it is the fallback")
    assert handoff < manual, (
        "browser_goal must be introduced before the manual loop; otherwise the "
        "instructions read as 'drive it yourself', which is exactly the reading "
        "that made every host do the clicking itself")


def test_the_manual_loop_is_marked_as_the_fallback():
    """A host needs the *condition*, not just the ordering, or it cannot choose.

    Without this it has two paths and no rule for picking between them, which is
    how the manual path wins by default.
    """
    assert "turbo_unavailable" in INSTRUCTIONS, (
        "name the one answer that means 'the handoff is unavailable', so the "
        "fallback is a decision and not a habit")


def test_the_element_table_is_still_explained():
    """Both remaining readers need the legend: the fallback path, and a host
    reading the final state after a goal has run."""
    assert "e12 btn" in INSTRUCTIONS
    assert "⊘" in INSTRUCTIONS


def test_the_handoff_is_stated_as_the_rule_for_tasks():
    """Not an option and not "an optimisation on top of" the loop.

    The design is that a browser *task* is handed over whole, so the wording has
    to be a rule a host follows by default rather than a capability it may
    notice. Softening this back into "you can also use browser_goal" is the
    regression this pins.
    """
    assert "Hand the task over." in INSTRUCTIONS, (
        "the instructions must state the handoff as the rule for a browser task")


def test_reading_a_page_is_excluded_from_the_handoff_rule():
    """Otherwise the rule swallows the free tools and a read costs a model call.

    A read is deterministic, offline and needs no key. Routing "what does this
    page say" through a decision model would spend money to answer a question
    the element table already answers, so the instructions have to draw the line
    between a task and a look.
    """
    assert "not a task" in INSTRUCTIONS, (
        "the instructions must say that reading a page is not a task")
    assert "need no key" in INSTRUCTIONS, (
        "and must say why the direct tools survive the rule")

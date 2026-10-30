"""
stream_splitter.py — splits the LLM token stream into tagged sections.

Expected model output:

    [WIDGET]...opaque widget payload...[/WIDGET]
    [SPEECH]...spoken narration...[/SPEECH]      (optional)
    [TEACHER]...on-screen teacher text...[/TEACHER]

The [WIDGET] payload is treated as opaque text: the splitter only looks for tag
boundaries and never parses what is inside.

split_stream() yields typed events (WidgetChunk, SpeechChunk, TeacherChunk) and
a final DoneEvent holding the full text of each section.

    for event in split_stream(generate_answer(..., stream=True)):
        if isinstance(event, WidgetChunk): ...
        elif isinstance(event, TeacherChunk): ...
        elif isinstance(event, DoneEvent): ...

Tolerance for imperfect model output (fail-open: text is never silently lost):
  - misspelt open/close tags are matched fuzzily (edit distance <= 2);
  - a missing [/WIDGET] is implied by the next [SPEECH] / [TEACHER];
  - a missing [WIDGET] is implied by [TEACHER], or by a ```interactive_svg fence;
  - text before any tag is kept (not trimmed) and flushed as teacher text if no
    tag ever arrives;
  - a section left open at end of stream is flushed as it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Generator, Literal


# ─────────────────────────── EVENT TYPES ───────────────────────

@dataclass
class WidgetChunk:
    """A fragment of the [WIDGET] section (opaque payload)."""
    text: str


@dataclass
class TeacherChunk:
    """A fragment of the [TEACHER] section (spoken narration text)."""
    text: str


@dataclass
class SpeechChunk:
    """A fragment of the [SPEECH] section (text to be spoken).

    [SPEECH] comes right after [/WIDGET] and before [TEACHER], so TTS can start
    as soon as the narration has streamed in. [TEACHER] is the on-screen text
    and is never sent to TTS directly. If the model emits no [SPEECH], no
    SpeechChunk is yielded and DoneEvent.speech is "": the caller then falls
    back to the teacher text for audio.
    """
    text: str


@dataclass
class DoneEvent:
    """Emitted once when both sections are fully parsed.

    Attributes:
        widget  — full assembled widget HTML/JS string
        teacher — full assembled teacher script string
        speech  — full assembled [SPEECH] string, or "" if the model
                  never emitted an optional [SPEECH] section this turn
    """
    widget: str
    teacher: str
    speech: str = ""


StreamEvent = WidgetChunk | TeacherChunk | SpeechChunk | DoneEvent


# ─────────────────────────── SPLITTER ──────────────────────────

_TEACHER_OPEN_RE  = re.compile(r'\[TEACHER\]')
_SPEECH_OPEN_RE   = re.compile(r'\[SPEECH\]')
_WIDGET_CLOSE_RE  = re.compile(r'\[/WIDGET\]')
_TEACHER_CLOSE_RE = re.compile(r'\[/TEACHER\]')

# ── Fuzzy closing-tag detection ──────────────────────────────────
# An exact match never catches a typo such as "[/TECHER]": the parser would stay
# in "teacher" mode and the tag text would leak into the content.
# _find_close_tag() accepts any bracketed word within a small edit distance of
# the target tag name (TEACHER / WIDGET / SPEECH).
_CLOSE_TAG_RE = re.compile(r'\[/\s*([A-Za-z]{3,12})\s*\]')


def _edit_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _find_close_tag(buffer: str, target: str, max_dist: int = 2):
    """Return the earliest closing tag in `buffer` within `max_dist` edits of
    `target` (e.g. "TEACHER"), or None. The first letter must match, so
    unrelated bracketed words are not mistaken for a tag."""
    best = None
    for m in _CLOSE_TAG_RE.finditer(buffer):
        word = m.group(1).upper()
        if word[0] != target[0]:
            continue
        if abs(len(word) - len(target)) > max_dist:
            continue
        if _edit_distance(word, target) <= max_dist:
            if best is None or m.start() < best.start():
                best = m
    return best


# ── Fuzzy OPEN-tag detection ─────────────────────────────────────
# Same idea as _find_close_tag(), for opening tags, so a mangled "[WIDGET]" or
# "[TEACHER]" is still recognised.
_OPEN_TAG_RE = re.compile(r'\[\s*([A-Za-z]{3,12})\s*\]')

# Fence that starts the mindmap template (see prompt_builder._mindmap_template()).
# Used as a last-resort "start of widget" signal when a summary turn emits the
# fence without the [WIDGET] wrapper.
_INTERACTIVE_SVG_FENCE = "```interactive_svg"


def _find_open_tag(buffer: str, target: str, max_dist: int = 2):
    """Like _find_close_tag() but for opening tags (no leading '/')."""
    best = None
    for m in _OPEN_TAG_RE.finditer(buffer):
        word = m.group(1).upper()
        if word[0] != target[0]:
            continue
        if abs(len(word) - len(target)) > max_dist:
            continue
        if _edit_distance(word, target) <= max_dist:
            if best is None or m.start() < best.start():
                best = m
    return best


def split_stream(
    raw_stream: Generator[str, None, None],
) -> Generator[StreamEvent, None, None]:
    """
    Consume a raw LLM token stream and yield typed StreamEvents.

    - Text before the first tag is kept, not discarded (see module docstring).
    - [WIDGET] ... [/WIDGET]   -> WidgetChunk events
    - [SPEECH] ... [/SPEECH]   -> SpeechChunk events (optional)
    - [TEACHER] ... [/TEACHER] -> TeacherChunk events
    - A single DoneEvent at the very end carries the full text of each section.

    A tag split across tokens is handled by withholding the last TAIL_GUARD
    characters of the buffer while a closing tag might be hiding there.
    """
    buffer: str = ""
    # Parser states:
    #   pre     waiting for [WIDGET] (or [TEACHER] directly)
    #   widget  accumulating the widget payload
    #   inter   after [/WIDGET]: waiting for [SPEECH], or [TEACHER] if skipped
    #   speech  accumulating [SPEECH]
    #   inter2  after [/SPEECH]: waiting for [TEACHER]
    #   teacher accumulating [TEACHER]
    #   done    [/TEACHER] seen, nothing more expected
    mode: Literal["pre", "widget", "inter", "speech", "inter2",
                  "teacher", "done"] = "pre"

    widget_parts:  list[str] = []
    teacher_parts: list[str] = []
    speech_parts:  list[str] = []

    # How many tail chars to withhold when a closing tag might be split.
    # Max tag length we search for is len("[/TEACHER]") = 10.
    TAIL_GUARD = 10

    for token in raw_stream:
        buffer += token

        # Keep looping until no more boundary can be found in the buffer.
        while True:

            # ── PRE: waiting for [WIDGET] (or [TEACHER] directly) ──
            if mode == "pre":
                widget_m  = _find_open_tag(buffer, "WIDGET")
                teacher_m = _find_open_tag(buffer, "TEACHER")
                widget_idx  = widget_m.start()  if widget_m  else -1
                teacher_idx = teacher_m.start() if teacher_m else -1

                if widget_idx != -1 and (teacher_idx == -1 or widget_idx <= teacher_idx):
                    # [WIDGET] found at or before any [TEACHER] in the buffer.
                    buffer = buffer[widget_m.end():]
                    mode = "widget"
                    continue

                if teacher_idx != -1:
                    # [WIDGET] skipped: go straight to teacher mode so the
                    # whole teacher section is captured.
                    buffer = buffer[teacher_m.end():]
                    mode = "teacher"
                    continue

                fence_idx = buffer.find(_INTERACTIVE_SVG_FENCE)
                if fence_idx != -1:
                    # No tag found, but the interactive_svg fence is present:
                    # the model wrote summary-mode widget content without the
                    # [WIDGET] wrapper. Treat the fence as the start of the
                    # widget instead of dumping raw HTML/JS into the chat.
                    # Prose before the fence is emitted as teacher text.
                    lead = buffer[:fence_idx]
                    if lead.strip():
                        teacher_parts.append(lead)
                        yield TeacherChunk(text=lead)
                    buffer = buffer[fence_idx:]
                    mode = "widget"
                    continue

                # Neither tag found yet: keep the whole buffer (no trimming), so
                # that if no tag ever arrives the end-of-stream flush can
                # recover the model's full answer, not just its last characters.
                break

            # ── WIDGET: accumulate until [/WIDGET] or [TEACHER] ──
            elif mode == "widget":
                close_m   = _WIDGET_CLOSE_RE.search(buffer)
                speech_m  = _SPEECH_OPEN_RE.search(buffer)
                teacher_m = _TEACHER_OPEN_RE.search(buffer)
                # earliest of [SPEECH] / [TEACHER] (whichever the model jumps
                # to first if it skips [/WIDGET])
                implicit_m, implicit_mode = None, None
                for cand_m, cand_mode in ((speech_m, "speech"), (teacher_m, "teacher")):
                    if cand_m is not None and (implicit_m is None or cand_m.start() < implicit_m.start()):
                        implicit_m, implicit_mode = cand_m, cand_mode

                if close_m is not None and (implicit_m is None or close_m.start() <= implicit_m.start()):
                    # Normal case: explicit [/WIDGET] (at or before any
                    # implicit-close tag in the buffer).
                    chunk = buffer[:close_m.start()]
                    if chunk:
                        widget_parts.append(chunk)
                        yield WidgetChunk(text=chunk)
                    buffer = buffer[close_m.end():]
                    mode = "inter"
                    continue

                if implicit_m is not None:
                    # [/WIDGET] skipped: close the widget right before the
                    # [SPEECH] / [TEACHER] tag so nothing is absorbed.
                    chunk = buffer[:implicit_m.start()]
                    if chunk:
                        widget_parts.append(chunk)
                        yield WidgetChunk(text=chunk)
                    buffer = buffer[implicit_m.end():]
                    mode = implicit_mode
                    continue

                # No closing tag yet: emit the safe prefix, keep the tail guard.
                safe_len = max(0, len(buffer) - TAIL_GUARD)
                if safe_len > 0:
                    chunk = buffer[:safe_len]
                    widget_parts.append(chunk)
                    yield WidgetChunk(text=chunk)
                    buffer = buffer[safe_len:]
                break

            # ── INTER: wait for [SPEECH] (or [TEACHER] if the model skips
            # it, which keeps older prompts without [SPEECH] working) ──
            elif mode == "inter":
                speech_m  = _find_open_tag(buffer, "SPEECH")
                teacher_m = _find_open_tag(buffer, "TEACHER")
                speech_idx  = speech_m.start()  if speech_m  else -1
                teacher_idx = teacher_m.start() if teacher_m else -1

                if speech_idx != -1 and (teacher_idx == -1 or speech_idx <= teacher_idx):
                    buffer = buffer[speech_m.end():]
                    mode = "speech"
                    continue

                if teacher_idx != -1:
                    # No [SPEECH] before [TEACHER] — model skipped it.
                    buffer = buffer[teacher_m.end():]
                    mode = "teacher"
                    continue

                # No trimming here either (see "pre" mode).
                break

            # ── SPEECH: accumulate until [/SPEECH] (fuzzy) ──────────
            elif mode == "speech":
                close_m = _find_close_tag(buffer, "SPEECH", max_dist=2)
                if close_m is None:
                    safe_len = max(0, len(buffer) - TAIL_GUARD)
                    if safe_len > 0:
                        chunk = buffer[:safe_len]
                        speech_parts.append(chunk)
                        yield SpeechChunk(text=chunk)
                        buffer = buffer[safe_len:]
                    break
                chunk = buffer[:close_m.start()]
                if chunk:
                    speech_parts.append(chunk)
                    yield SpeechChunk(text=chunk)
                buffer = buffer[close_m.end():]
                mode = "inter2"
                continue

            # ── INTER2: skip whitespace, wait for [TEACHER] ──────────
            elif mode == "inter2":
                m = _TEACHER_OPEN_RE.search(buffer)
                if not m:
                    # No trimming (see "pre" mode): if [TEACHER] never comes,
                    # the leftover buffer is recovered at end of stream.
                    break
                buffer = buffer[m.end():]
                mode = "teacher"

            # ── TEACHER: accumulate until [/TEACHER] (fuzzy) ──
            elif mode == "teacher":
                close_m = _find_close_tag(buffer, "TEACHER", max_dist=2)
                if close_m is None:
                    safe_len = max(0, len(buffer) - TAIL_GUARD)
                    if safe_len > 0:
                        chunk = buffer[:safe_len]
                        teacher_parts.append(chunk)
                        yield TeacherChunk(text=chunk)
                        buffer = buffer[safe_len:]
                    break
                chunk = buffer[:close_m.start()]
                if chunk:
                    teacher_parts.append(chunk)
                    yield TeacherChunk(text=chunk)
                buffer = buffer[close_m.end():]
                mode = "done"
                break   # nothing more expected after [TEACHER] closes

            # ── DONE: nothing more expected ─────────────────────────
            else:
                break

    # ── End-of-stream flush ──────────────────────────────────────────
    # For a missing closing tag (truncated response, token limit, ...): whatever
    # is left in the buffer for the current mode is flushed instead of lost.
    if mode == "widget" and buffer.strip():
        widget_parts.append(buffer)
        yield WidgetChunk(text=buffer)

    elif mode == "teacher" and buffer.strip():
        teacher_parts.append(buffer)
        yield TeacherChunk(text=buffer)

    # "inter": no [SPEECH] or [TEACHER] after [/WIDGET] (the model went straight
    # into prose). The buffer holds the full post-widget text: surface it as
    # teacher text.
    elif mode == "inter" and buffer.strip():
        teacher_parts.append(buffer)
        yield TeacherChunk(text=buffer)

    # [SPEECH] opened but never closed (truncated): flush what is left.
    elif mode == "speech" and buffer.strip():
        speech_parts.append(buffer)
        yield SpeechChunk(text=buffer)

    # "inter2": [/SPEECH] closed but [TEACHER] never came: surface the rest as
    # teacher text.
    elif mode == "inter2" and buffer.strip():
        teacher_parts.append(buffer)
        yield TeacherChunk(text=buffer)

    # "pre": no [WIDGET] or [TEACHER] at all. The buffer holds the model's full
    # response, which is shown as teacher text instead of a blank bubble. This is
    # a safety net: the real fix for tag-less output belongs in the prompt.
    elif mode == "pre" and buffer.strip():
        teacher_parts.append(buffer)
        yield TeacherChunk(text=buffer)

    yield DoneEvent(
        widget  = "".join(widget_parts).strip(),
        teacher = "".join(teacher_parts).strip(),
        speech  = "".join(speech_parts).strip(),
    )
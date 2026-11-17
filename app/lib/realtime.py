"""Real-time layer of the meeting copilot.

- Finding questions, three ways at once (measured on two recorded meetings: keywords alone
  found 4 of 9 questions, the batch check 7 of 9, together 8 of 9):
  the user's name -> answered at once; a question keyword -> answered at once;
  every other line -> QuestionWindow sends the last few seconds of lines to the fast model in one
  call, and the lines it marks are answered. One call per line was tried and was less accurate.
- An urgent explanation worker: asks the answer model and streams the answer
  into the live notes as it is generated; a question to the user gets a suggested
  reply direction. Optional background verification with web search appends a
  correction or addition only when it adds something.
"""
import queue
import re
import threading
import time
from collections import deque
from datetime import datetime

import i18n
import llm

WORKERS = 3
ANSWER_TIMEOUT_SECS = 90   # a hung call to the model is stopped; otherwise it would hold the stream lock forever
STALE_SECS = 60          # a question still queued after this is dropped: the moment has passed
# Questions answered this long ago or less go into the prompt, so a question asked again in other words is skipped:
# a restatement can score well under the similar() thresholds and would otherwise be answered again.
JUST_ANSWERED_SECS = 180
WINDOW_SECS = 4          # QuestionWindow: how long lines are collected before one check
WINDOW_MAX_LINES = 8     # ... or check as soon as this many lines are waiting
MIN_CHARS = 4            # shorter lines ("嗯", "對對") are not sent unless a pattern matches

# Where the model is reached, and how Claude Code is called: llm.py. The names stay here for the tools that use them.
SANDBOX, SYSTEM_PROMPT, BASE_ARGS, WEB_TOOLS, sandbox = llm.SANDBOX, llm.SYSTEM_PROMPT, llm.BASE_ARGS, llm.WEB_TOOLS, llm.sandbox


QUESTION_RE = re.compile(
    r"[?？]|嗎|吗|呢$|什麼|什么|甚麼|啥|怎麼|怎么|怎樣|怎样|為什麼|为什么|為何|为何|差在哪|差別|差别|哪裡|哪里|哪個|哪个|"
    r"哪些|是不是|有沒有|有没有|能不能|可不可以|要不要|對不對|对不对|好不好|多少|多久|幾個|几个|幾天|几天|誰|谁|"
    r"(?<![A-Za-z0-9])(?:what|why|how|which|when|where|who|difference|vs\.?)(?![A-Za-z0-9])",
    re.IGNORECASE)   # not \b: Chinese characters count as word characters, so "這個why" has no boundary

def tag_of(line):
    """The speaker tag of a "[tag] text" line, or None."""
    m = re.match(r"\[([^\]]+)\]", line or "")
    return m.group(1) if m else None


PREVIOUS_LINES = 6     # how far back the one who spoke before the trigger line's speaker is looked for


def previous_speaker(context, line, unknown=None):
    """The tag of the last one to speak before the trigger line's speaker, within the PREVIOUS_LINES lines before
    it, or None. Lines tagged `unknown` are passed over: a short line whose voice was not told is anyone but the
    user, the trigger line's speaker included, so it says nothing about who spoke."""
    own = tag_of(line)
    for earlier in reversed((context or "").splitlines()[-PREVIOUS_LINES:]):
        tag = tag_of(earlier)
        if tag and tag not in (own, unknown):
            return tag
    return None


def previous_hint(lang, user, context, line, table=None):
    """The prompt's note on who spoke last before the trigger line ("" when that is not known): a bare "you" is
    mostly said to them (otherwise questions put to the one who has just finished speaking are shown as asked of
    the user). `table` is another version's i18n.PREVIOUS_HINT, for the evaluation tools."""
    prev = previous_speaker(context, line, unknown=lang.speaker(i18n.OTHERS))
    if prev is None:
        return ""
    key = "previous_me" if prev == lang.speaker(i18n.SELF) else "previous_other"
    return (table or i18n.PREVIOUS_HINT)[lang.family][key].format(prev=prev, name=user)


def mentions(text, names):
    """Does the line address one of these names? ASCII names must stand on their own ("Al" is not "also"). Chinese
    names match in either script: a name typed 陳偉 is heard in a Simplified transcript as 陈伟."""
    low = i18n.fold_script(text).lower()
    for n in names:
        if not n:
            continue
        n = i18n.fold_script(n)
        if n.isascii():
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(n.lower())}(?![A-Za-z0-9])", low):
                return True
        elif n.lower() in low:
            return True
    return False


def looks_like_question(text):
    return bool(QUESTION_RE.search(text))


# A line made only of these says nothing in the notes ("對", "呃", "好好", "對然後", "ok ok"), and such lines are
# common. Lines with anything else ("就這樣", "聽得到嗎", "在樓上") are kept.
FILLER_RE = re.compile(
    r"(?:對|对|好的|好啦|好|是的|是|呃|哦|喔|噢|嗯|恩|啊|呀|欸|誒|诶|唉|哎|呵|哈|喂|啦|然後|然后|就是|那個|那个|那|"
    r"okay|ok|yeah|yes|yep|uhm|uh|um|hmm|mm|oh|ah)+")


def filler_only(text):
    """Only filler words, ignoring spaces and punctuation; an empty line is not filler."""
    bare = re.sub(r"[\W_]+", "", text).lower()
    return bool(bare) and bool(FILLER_RE.fullmatch(bare))


def worth_asking(text):
    """Send a line to the fast model unless it is too short to hold a question."""
    return looks_like_question(text) or len(re.sub(r"[\W_]+", "", text)) >= MIN_CHARS


class QuestionWindow:
    """Batch check for questions without a keyword: every few seconds, one fast-model call over the new
    lines (with the lines before them as context); each marked line goes to the explainer."""

    def __init__(self, llm, model, user, aliases, lang, explainer, log):
        self.llm, self.model, self.user, self.aliases = llm, model, user, aliases
        self.lang, self.explainer, self.log = lang, explainer, log
        self.pending = []            # (line, context, screen)
        self.recent = deque(maxlen=20)
        self.lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    def add(self, line, screen):
        """A line not already sent to the explainer."""
        with self.lock:
            self.pending.append((line, "\n".join(self.recent), screen))
            self.recent.append(line)

    def seen(self, line):
        """A line handled elsewhere (name or keyword): context only."""
        with self.lock:
            self.recent.append(line)

    def reset(self):
        """A new meeting: forget the previous one's lines."""
        with self.lock:
            self.pending.clear()
            self.recent.clear()

    def _loop(self):
        first_at = None
        while True:
            time.sleep(0.5)
            with self.lock:
                if not self.pending:
                    first_at = None
                    continue
                first_at = first_at or time.time()
                if time.time() - first_at < WINDOW_SECS and len(self.pending) < WINDOW_MAX_LINES:
                    continue
                batch, self.pending, first_at = self.pending, [], None
            try:
                self._check(batch)
            except Exception as e:  # noqa: BLE001 - never kill the loop
                self.log(f"question window error: {e!r}")

    def _check(self, batch):
        none = self.lang.t("none")
        prompt = i18n.WINDOW_PROMPT[self.lang.family].format(
            name=self.user, aliases=self.lang.t("list_sep").join(self.aliases), context=batch[0][1] or none,
            new="\n".join(f"{n} {line}" for n, (line, _, _) in enumerate(batch, 1)))
        t0 = time.time()
        try:
            out = self.llm.complete(prompt, self.model, timeout=60)
        except llm.LLMError as e:
            self.log(f"question window: the model failed {e}")
            return
        marked = []
        for out_line in out.splitlines():
            parts = out_line.strip().split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].upper() in ("M", "Q") \
                    and 1 <= int(parts[0]) <= len(batch):
                line, context, screen = batch[int(parts[0]) - 1]
                marked.append(line)
                self.explainer.submit(0 if parts[1].upper() == "M" else 1, line, context, screen, judged=True)
        self.log(f"question window: {len(batch)} lines, {len(marked)} marked, {time.time() - t0:.1f}s"
                 + (f": {marked[0][:40]}" if marked else ""))


class UrgentExplainer:
    """Priority worker: questions to the user first, then other questions. Streams into the notes."""

    def __init__(self, llm, model, verify_model, user, aliases, detail, known_terms, web_verify,
                 max_per_minute, write, notify, log, remember, stream_lock, lang):
        self.llm, self.model, self.verify_model = llm, model, verify_model
        self.lang = lang
        self.user, self.aliases, self.detail = user, aliases, lang.detail(detail)
        self.known_terms, self.web_verify = known_terms, web_verify
        self.max_per_minute = max_per_minute
        self.write, self.notify, self.log, self.remember = write, notify, log, remember
        self.stream_lock = stream_lock       # one answer streams at a time; others are written whole
        self.q = queue.PriorityQueue()
        self.recent = deque()        # times of meeting questions answered (for the per-minute cap)
        self.answered = set()        # trigger lines already answered: the batch check may send them again
        self.answers = deque()       # (time, restated question) answered, for just_answered()
        self.seq = 0
        self._lock = threading.Lock()   # recent, answered and seq are touched by the feed and the workers
        for _ in range(WORKERS):      # several at once, so a question to the user never waits behind another
            threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, priority, line, context, screen, judged=False):
        """priority 0 = names the user, 1 = a question.
        judged: QuestionWindow already marked it, so the answer step does not second-guess it."""
        with self._lock:
            if line in self.answered:
                return
            self.seq += 1
            seq = self.seq
        self.q.put((priority, seq, line, context, screen, time.time(), judged, self.generation))

    generation = 0     # bumped by reset(): an answer still streaming for the old meeting stops writing

    def reset(self):
        """A new meeting: previous questions may be asked again, and queued answers are dropped."""
        with self._lock:
            self.answered.clear()
            self.answers.clear()
            self.recent.clear()
            self.generation += 1
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass

    def _claim(self, line):
        """True once per line, whichever path (fast or batch check) gets there first."""
        with self._lock:
            if line in self.answered:
                return False
            self.answered.add(line)
            return True

    def just_answered(self):
        """The questions answered in the last JUST_ANSWERED_SECS, for the prompt ("" when none)."""
        with self._lock:
            now = time.time()
            while self.answers and now - self.answers[0][0] > JUST_ANSWERED_SECS:
                self.answers.popleft()
            return self.lang.t("list_sep").join(q for _, q in self.answers)

    def _count_question(self):
        """Count a meeting question towards the per-minute cap; False when the cap is reached."""
        with self._lock:
            now = time.time()
            while self.recent and now - self.recent[0] > 60:
                self.recent.popleft()
            if len(self.recent) >= self.max_per_minute:
                return False
            self.recent.append(now)
            return True

    def _loop(self):
        while True:
            priority, _, line, context, screen, queued_at, judged, gen = self.q.get()
            if gen != self.generation:
                continue                      # queued for a meeting that has ended
            if priority >= 1 and time.time() - queued_at > STALE_SECS:
                self.log(f"explain: dropped stale question ({time.time() - queued_at:.0f}s): {line[:40]}")
                continue
            try:
                self._answer(priority, line, context, screen, queued_at, judged, gen)
            except Exception as e:  # noqa: BLE001 - never kill the worker
                self.log(f"explain error: {e!r}")

    def _answer(self, priority, line, context, screen, queued_at, judged=False, gen=None):
        none = self.lang.t("none")
        prompt = i18n.URGENT_PROMPT[self.lang.family].format(
            name=self.user, detail=self.detail, known=self.lang.t("list_sep").join(self.known_terms) or none,
            context=context or none, screen=screen or none, line=line, answered=self.just_answered() or none,
            hint=(i18n.JUDGED_HINT[self.lang.family] if judged else "") + self._previous_hint(context, line))
        t0 = time.time()
        state = {"streaming": False}       # set by _stream the moment it takes the lock
        try:
            stream = self.llm.stream(prompt, self.model)
        except llm.LLMError as e:
            self.log(f"explain: the model failed {e}")
            return
        buf, header_done, kind, question, answer = "", False, None, "", ""
        first_token = header_at = None
        watchdog = threading.Timer(ANSWER_TIMEOUT_SECS, stream.close)
        watchdog.start()
        try:
            header_done, kind, question, answer, first_token, header_at, _ = self._stream(stream, t0, line, state, gen)
        finally:
            watchdog.cancel()
            if state["streaming"]:
                self.stream_lock.release()
            stream.close()
        if not header_done or (gen is not None and gen != self.generation):
            return
        self.log(f"explain: {kind} first token {first_token:.1f}s, header {header_at:.1f}s, "
                 f"done {time.time() - t0:.1f}s (queued {t0 - queued_at:.1f}s): {question[:40]}"
                 + (f" ({state['addressee'][:40]})" if kind == "to_me" and state.get("addressee") else ""))
        self.notify(f"{self.label(kind)} {question}", answer.strip()[:200], kind="explain")
        if self.web_verify and self.llm.web and kind == "question":
            threading.Thread(target=self._verify, args=(question, answer), daemon=True).start()

    def _previous_hint(self, context, line):
        return previous_hint(self.lang, self.user, context, line)

    def _stream(self, stream, t0, line, state, gen=None):
        """Read the model's streamed reply: header first, then the answer (streamed if no other answer is)."""
        buf, header_done, kind, question, answer = "", False, None, "", ""
        first_token = header_at = None
        streaming = False

        def write(text):
            if gen is None or gen == self.generation:    # never into the next meeting's notes
                self.write(text)
        for text in stream:
            first_token = first_token or time.time() - t0
            buf += text
            if not header_done:
                # Header = whom the line was said to, the kind, and the restated question (i18n.Lang.header).
                complete = [l.strip() for l in buf.split("\n")[:-1] if l.strip()]
                head = i18n.Lang.header(complete)
                if head is None:
                    continue
                kind, question, used, addressee = head
                state["addressee"] = addressee
                if kind == "to_me" and addressee and not mentions(addressee, (self.user, *self.aliases)):
                    kind = "question"          # said to someone else, whatever the kind line says
                if kind == "skip":
                    stream.close()
                    self.log(f"explain: skipped in {time.time() - t0:.1f}s: {line[:40]}")
                    return False, None, "", "", first_token, None, False
                if not self._claim(line):          # the same line, sent by two paths at once
                    stream.close()
                    return False, None, "", "", first_token, None, False
                if kind == "question" and not self._count_question():
                    stream.close()
                    self.log(f"explain: rate limit, skipped: {question[:40]}")
                    return False, None, "", "", first_token, None, False
                self.remember(f"answered: {question}")   # register now so the periodic tick sees it
                with self._lock:
                    self.answers.append((time.time(), question))
                label = self.label(kind)
                streaming = self.stream_lock.acquire(blocking=False)
                state["streaming"] = streaming
                held = [] if not streaming else None
                out = write if streaming else held.append
                out(f"\n### {label} {question}\n")
                self.notify(f"{label} {question}", self.lang.t("answer_generating"), kind="explain")
                header_at = time.time() - t0
                rest = buf.split("\n")
                idx = [i for i, l in enumerate(rest) if l.strip()][used - 1] + 1     # after the question line
                header_done, buf = True, "\n".join(rest[idx:]).lstrip("\n")
            if header_done and buf:
                out(buf)
                answer += buf
                buf = ""
        if not header_done:
            return False, kind, question, answer, first_token, header_at, False
        out("\n")
        if not streaming:
            with self.stream_lock:
                write("".join(held))
        return header_done, kind, question, answer, first_token, header_at, streaming

    def label(self, kind):
        return self.lang.t("answer_to_me") if kind == "to_me" else self.lang.t("answer_heading")

    def _verify(self, question, answer):
        try:
            out = self.llm.complete(i18n.VERIFY_PROMPT[self.lang.family].format(question=question, answer=answer),
                                    self.verify_model, web=True, timeout=180).strip()
        except llm.LLMError as e:
            self.log(f"verify failed {e}")
            return
        if not out or out.upper().startswith("OK"):
            return
        label = i18n.VERIFY_LABEL[self.lang.family]
        if i18n.chinese(self.lang.family):
            text, title = f"  {label}（{question[:20]}）：{out}\n", f"{label}：{question[:30]}"
        else:
            text, title = f"  {label} ({question[:20]}): {out}\n", f"{label}: {question[:30]}"
        held = self.stream_lock.acquire(timeout=30)      # not in the middle of a streaming answer
        try:
            self.write(text)
        finally:
            if held:
                self.stream_lock.release()
        self.notify(title, out[:200], kind="explain")


def _norm(text):
    return re.sub(r"[\W_]+", "", text.lower())


def similar(a, b):
    """True if two items/questions say the same thing. Deliberately conservative: a missed duplicate
    costs one extra line, a false match hides a real item. Thresholds tuned on hand-labelled item pairs:
    duplicates ratio >= 0.64 and bigram overlap >= 0.43; distinct items sharing names <= 0.59 / 0.39."""
    from difflib import SequenceMatcher
    a, b = _norm(a), _norm(b)
    if len(a) < 2 or len(b) < 2:
        return a == b
    ga = {a[i:i + 2] for i in range(len(a) - 1)}
    gb = {b[i:i + 2] for i in range(len(b) - 1)}
    jaccard = len(ga & gb) / len(ga | gb)
    return SequenceMatcher(None, a, b).ratio() >= 0.62 and jaccard >= 0.4


def load_known_terms(path):
    try:
        with open(path) as f:
            return [t.strip() for t in f if t.strip() and not t.startswith("#")]
    except OSError:
        return []


def stamp(ts):
    """'2001-02-03T04:05:06' -> '04:05:06'."""
    return ts[11:19] if len(ts) >= 19 else datetime.now().strftime("%H:%M:%S")

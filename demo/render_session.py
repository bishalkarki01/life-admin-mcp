"""Render a real Claude Code + life-admin-mcp session transcript (stream-json) into a terminal-style video.

Everything shown comes from the transcripts: the user prompts, tool names, tool arguments,
tool results and Claude's replies. The only edit is shortening the temporary statement path.
"""

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

SRC = Path(sys.argv[1])  # folder with t1.jsonl..tN.jsonl
OUT = Path(sys.argv[2])  # output folder
TURNS = [int(x) for x in sys.argv[3].split(",")]
REAL_DIR = str(SRC)
SHORT = "~/Downloads"

W, H = 1280, 720
FPS = 15
PAD_X, TOP, BOTTOM = 36, 64, 52
FONT = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 17)
FONT_B = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 17, index=1)
UI = ImageFont.truetype("/System/Library/Fonts/SFNS.ttf", 17)
UI_B = ImageFont.truetype("/System/Library/Fonts/SFNS.ttf", 30)
LH = 24
CW = FONT.getlength("M")
COLS = int((W - 2 * PAD_X) // CW)
ROWS = (H - TOP - BOTTOM) // LH

BG = (24, 26, 31)
FG = (214, 218, 224)
DIM = (128, 136, 148)
USER = (120, 200, 255)
TOOL = (229, 192, 123)
OK = (152, 195, 121)
BOLD = (255, 255, 255)
ACCENT = (11, 93, 99)

prompts = {
    1: "Here's my bank export for the last year: ~/Downloads/statement.csv. Find all my subscriptions.",
}


def clean(s: str) -> str:
    return s.replace(REAL_DIR, SHORT)


def load_turn(n: int):
    """Return (prompt, [events]) where events are ('tool', name, args, result) or ('text', str)."""
    prompt = None
    events = []
    pending = {}
    for line in open(SRC / f"t{n}.jsonl", encoding="utf-8"):
        e = json.loads(line)
        if e.get("type") == "assistant":
            for c in e["message"]["content"]:
                if c["type"] == "text":
                    events.append(["text", c["text"]])
                elif c["type"] == "tool_use" and c["name"].startswith("mcp__life-admin__"):
                    ev = ["tool", c["name"].split("__")[-1], c["input"], ""]
                    pending[c["id"]] = ev
                    events.append(ev)
        elif e.get("type") == "user":
            for c in e["message"]["content"]:
                if isinstance(c, dict) and c.get("type") == "tool_result" and c["tool_use_id"] in pending:
                    cc = c["content"]
                    s = cc if isinstance(cc, str) else " ".join(x.get("text", "") for x in cc if isinstance(x, dict))
                    try:
                        s = json.loads(s).get("result", s)
                    except (ValueError, AttributeError):
                        pass
                    pending[c["tool_use_id"]][3] = s
    return prompt, events


def user_prompts():
    """Prompts as sent (turn 1's path shortened). Turns 2+ were passed on the command line."""
    return {
        1: prompts[1],
        2: "Track all of them except PG&E and Verizon. Also, I just started a Duolingo free trial "
        "that ends Dec 10, then it's $12.99 a month.",
        3: "I used Netflix and Spotify this week. What's coming up in the next two weeks?",
        4: "What could I cancel to save money?",
        5: "Put it all on my calendar.",
    }


# ------------------------------------------------------------------ line model
# A line is a list of (text, colour, bold) spans.
def wrap_plain(text, colour, indent=""):
    out = []
    for para in text.split("\n"):
        if not para.strip():
            out.append([("", colour, False)])
            continue
        for w in textwrap.wrap(para, COLS - len(indent), subsequent_indent="", break_long_words=True) or [""]:
            out.append([(indent + w, colour, False)])
    return out


def md_spans(line, colour=FG):
    spans = []
    parts = re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", line)
    for p in parts:
        if not p:
            continue
        if p.startswith("**"):
            spans.append((p[2:-2], BOLD, True))
        elif p.startswith("`"):
            spans.append((p[1:-1], TOOL, False))
        else:
            spans.append((p, colour, False))
    return spans


def md_lines(text):
    """Very small markdown renderer: tables become aligned columns, bullets keep a marker."""
    out = []
    lines = clean(text).split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            widths = [max(len(re.sub(r"\*\*|`", "", r[k])) for r in rows if k < len(r)) for k in range(len(rows[0]))]
            for r_i, r in enumerate(rows):
                spans = [("  ", FG, False)]
                for k, cell in enumerate(r):
                    vis = len(re.sub(r"\*\*|`", "", cell))
                    cs = md_spans(cell, DIM if r_i == 0 else FG)
                    if r_i == 0:
                        cs = [(t, DIM, True) for t, _, _ in cs]
                    spans += cs + [(" " * (widths[k] - vis + 3), FG, False)]
                out.append(spans)
                if r_i == 0:
                    out.append([("  " + "─" * (sum(widths) + 3 * len(widths) - 3), DIM, False)])
            continue
        if not ln.strip():
            out.append([("", FG, False)])
        else:
            bullet = ln.lstrip().startswith("- ")
            body = ln.lstrip()[2:] if bullet else ln
            prefix = "  • " if bullet else "  "
            for j, w in enumerate(textwrap.wrap(body, COLS - len(prefix) - 2) or [""]):
                out.append([((prefix if j == 0 else " " * len(prefix)), DIM, False)] + md_spans(w))
        i += 1
    return out


def fmt_args(name, args):
    a = clean(json.dumps(args, ensure_ascii=False))
    a = a.replace('"', "").replace("{", "").replace("}", "").replace(": ", "=")
    return f"{name}({a})"


def tool_lines(name, args, result, compact=False):
    head = [("● ", OK, False), ("life-admin", TOOL, True), (" · ", DIM, False)]
    call = fmt_args(name, args)
    if compact:
        first = clean(result).split("\n")[1] if "\n" in result else clean(result)
        first = re.sub(r"\s+", " ", first)
        call = call if len(call) <= COLS - 16 else call[: COLS - 17] + "…"
        return [head + [(call, FG, False)], [("  └ ", DIM, False), (first[: COLS - 6], DIM, False)]]
    out = []
    for j, w in enumerate(textwrap.wrap(call, COLS - 16)):
        out.append((head if j == 0 else [("    ", FG, False)]) + [(w, FG, False)])
    res = [r for r in clean(result).split("\n") if r.strip()]
    show = res[:7]
    for j, r in enumerate(show):
        for k, w in enumerate(textwrap.wrap(r, COLS - 6) or [""]):
            out.append([(("  └ " if j == 0 and k == 0 else "    "), DIM, False), (w, DIM, False)])
    if len(res) > len(show):
        out.append([("    … +" + str(len(res) - len(show)) + " lines", DIM, False)])
    return out


# ------------------------------------------------------------------ frames
class Screen:
    def __init__(self):
        self.lines = []
        self.frames = 0
        cmd = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{W}x{H}",
            "-r",
            str(FPS),
            "-i",
            "-",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "slow",
            "-crf",
            "20",
            "-movflags",
            "+faststart",
            str(OUT / "real-session.mp4"),
        ]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)  # noqa: S603 - fixed ffmpeg command
        self.caption = ""

    def chrome(self, d):
        d.rectangle([0, 0, W, 40], fill=(36, 39, 46))
        for k, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            d.ellipse([18 + k * 22, 14, 30 + k * 22, 26], fill=c)
        t = "claude  —  life-admin-mcp 1.0.0 (installed from PyPI with uvx)"
        d.text(((W - UI.getlength(t)) / 2, 10), t, font=UI, fill=DIM)
        d.rectangle([0, H - 40, W, H], fill=(36, 39, 46))
        foot = "Real, unedited Claude Code session · made-up sample statement · file path shortened"
        d.text((PAD_X, H - 30), foot, font=UI, fill=DIM)
        if self.caption:
            d.text((W - PAD_X - UI.getlength(self.caption), H - 30), self.caption, font=UI, fill=OK)

    def draw(self, cursor=False):
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im)
        self.chrome(d)
        vis = self.lines[-ROWS:]
        y = TOP
        for spans in vis:
            x = PAD_X
            for text, col, bold in spans:
                f = FONT_B if bold else FONT
                d.text((x, y), text, font=f, fill=col)
                x += f.getlength(text)
            y += LH
        if cursor:
            d.rectangle([x + 2, y - LH + 4, x + 11, y - 4], fill=FG)
        return im

    def emit(self, n=1, cursor=False):
        b = self.draw(cursor).tobytes()
        for _ in range(n):
            self.proc.stdin.write(b)
            self.frames += 1

    def type_prompt(self, text):
        wrapped = textwrap.wrap(text, COLS - 4)
        self.lines.append([("", FG, False)])
        for j, w in enumerate(wrapped):
            self.lines.append([(("> " if j == 0 else "  "), USER, True), ("", USER, False)])
            for k in range(0, len(w) + 1, 3):
                self.lines[-1][1] = (w[:k], USER, False)
                self.emit(1, cursor=True)
            self.lines[-1][1] = (w, USER, False)
        self.emit(int(FPS * 0.6))
        self.lines.append([("", FG, False)])

    def add(self, lines, per_frame=2, hold=0.0):
        for k in range(0, len(lines), per_frame):
            self.lines += lines[k : k + per_frame]
            self.emit(1)
        self.emit(int(FPS * hold))

    def title(self, big, small, secs):
        for _ in range(int(FPS * secs)):
            im = Image.new("RGB", (W, H), BG)
            d = ImageDraw.Draw(im)
            d.rectangle([0, 0, 10, H], fill=ACCENT)
            d.text((90, 250), big, font=UI_B, fill=BOLD)
            for j, s in enumerate(small):
                d.text((90, 310 + j * 34), s, font=UI if j else FONT, fill=FG if j == 0 else DIM)
            b = im.tobytes()
            self.proc.stdin.write(b)
            self.frames += 1

    def close(self):
        self.proc.stdin.close()
        self.proc.wait()


OUT.mkdir(parents=True, exist_ok=True)
sc = Screen()
sc.title(
    "Life Admin MCP — a real session",
    [
        "claude mcp add life-admin -- uvx life-admin-mcp",
        "Claude Code using life-admin-mcp 1.0.0, installed from PyPI.",
        "Real prompts, real tool calls, real replies, unedited. The statement is the made-up sample from the repo.",
    ],
    4.0,
)
P = user_prompts()
labels = {
    1: "1 · scan a bank export",
    2: "2 · track + free trial",
    3: "3 · what's due",
    4: "4 · what could I cancel",
    5: "5 · calendar",
}
for n in TURNS:
    sc.caption = labels.get(n, "")
    sc.lines = []
    _, events = load_turn(n)
    sc.type_prompt(P[n])
    tools = [e for e in events if e[0] == "tool"]
    compact = len(tools) > 3
    for e in events:
        if e[0] == "tool":
            sc.add(
                tool_lines(e[1], e[2], e[3], compact), per_frame=1 if not compact else 2, hold=0.3 if compact else 1.5
            )
        else:
            sc.lines.append([("", FG, False)])
            sc.add([[("● ", BOLD, False)]] + md_lines(e[1]), per_frame=1, hold=0.0)
    sc.emit(int(FPS * 6.5))
sc.title(
    "Try it",
    [
        "uvx life-admin-mcp",
        "github.com/bishalkarki01/life-admin-mcp",
        "Independent open-source project · not financial advice",
    ],
    4.0,
)
sc.close()
print("frames", sc.frames, "seconds", round(sc.frames / FPS, 1))

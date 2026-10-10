#!/usr/bin/env python3
"""生成 README 演示 GIF 与 GitHub 社交预览图（docs/feature/feature-distribution.md §5.1.3）。

前置条件：
  - 在 macOS 上运行：字体固定用 /System/Library/Fonts/Menlo.ttc 与 Helvetica.ttc，找不到就报错退出，
    不回落到其它字体——换字体会让图片尺寸和排版悄悄变化。
  - Python 3.8+，已安装 Pillow（pip install Pillow）；不需要网络。
  - 从仓库任意目录运行均可：用本仓库 src/ 下的 multi-codex，输出默认写到仓库的 docs/assets/。

做法：在临时目录里建 HOME、假 codex 和示例账号的假 auth.json（work@example.com、me@example.com），
实际运行 multi-codex 抓取真实输出，再按终端样式逐帧绘制。临时目录结束时删除，不碰真实的 ~/.codex、~/.cx。
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONO_FONT = "/System/Library/Fonts/Menlo.ttc"
SANS_FONT = "/System/Library/Fonts/Helvetica.ttc"

# 终端配色：深色背景，提示符绿色、注释灰色、输出浅色。
BG = (30, 30, 46)
FG = (205, 214, 244)
PROMPT = (166, 227, 161)
COMMENT = (127, 132, 156)
TITLE_BAR = (49, 50, 68)

EXAMPLES = """examples:
  make-assets.py                         write docs/assets/demo.gif and social-preview.png
  make-assets.py --out /tmp/assets       write the two images to another directory
  make-assets.py --print                 only print the captured terminal session
"""


def fake_jwt(email, plan):
    claims = {"email": email, "https://api.openai.com/auth": {"chatgpt_plan_type": plan}}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return "eyJhbGciOiJub25lIn0." + payload + ".signature"


def capture_session():
    """在临时 HOME 中运行演示命令，返回 [(命令或注释, 输出行列表)]。"""
    tmp = tempfile.mkdtemp(prefix="mcx-assets-")
    try:
        home = os.path.join(tmp, "home")
        fakebin = os.path.join(tmp, "bin")
        os.makedirs(home)
        os.makedirs(fakebin)
        with open(os.path.join(fakebin, "codex"), "w") as handle:
            handle.write("#!/bin/sh\nexit 0\n")
        os.chmod(os.path.join(fakebin, "codex"), 0o755)
        # 启动命令目录放进 PATH：否则 add 会打印“不在 PATH 中”的警告，演示里不需要它。
        env = {"HOME": home, "PATH": os.pathsep.join([os.path.join(home, ".local", "bin"), fakebin, "/usr/bin", "/bin"]),
               "PYTHONPATH": os.path.join(ROOT, "src"), "LC_ALL": "en_US.UTF-8"}

        def run(*args):
            proc = subprocess.run([sys.executable, "-m", "multi_codex"] + list(args), env=env, cwd=tmp,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                                  stdin=subprocess.DEVNULL, check=True)
            return proc.stdout.rstrip("\n").splitlines()

        session = [("multi-codex add work --proxy 7901", run("add", "work", "--proxy", "7901")),
                   ("multi-codex add personal", run("add", "personal"))]
        # 示例账号的登录结果：真实使用时由 `multi-codex login NAME` 写入。
        for name, email, plan in (("work", "work@example.com", "team"), ("personal", "me@example.com", "plus")):
            data = {"auth_mode": "chatgpt", "tokens": {"id_token": fake_jwt(email, plan)}}
            path = os.path.join(home, ".cx", name, "auth.json")
            with open(path, "w") as handle:
                json.dump(data, handle)
        # 示例额度：真实使用时 Codex 把额度快照写进会话日志，list 的 USAGE 列只读本机这份记录。
        # 格式与 Codex 写的一致（紧凑 JSON，usage 按 `"rate_limits":{` 片段预过滤）。
        for name, five_hour, weekly in (("work", 23.0, 41.0), ("personal", 8.0, 15.0)):
            later = int(time.time()) + 3600
            limits = {"limit_id": "codex",
                      "primary": {"used_percent": five_hour, "window_minutes": 300, "resets_at": later},
                      "secondary": {"used_percent": weekly, "window_minutes": 10080, "resets_at": later + 86400}}
            record = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()), "type": "event_msg",
                      "payload": {"type": "token_count", "info": None, "rate_limits": limits}}
            log_dir = os.path.join(home, ".cx", name, "sessions", "2026", "01", "01")
            os.makedirs(log_dir)
            with open(os.path.join(log_dir, "rollout-demo.jsonl"), "w") as handle:
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        session.append(("# log in once per account: multi-codex login work", []))
        session.append(("multi-codex list", run("list")))
        session.append(("# codex-work and codex-personal now run side by side", []))
        return session
    finally:
        shutil.rmtree(tmp)


def load_fonts():
    for path in (MONO_FONT, SANS_FONT):
        if not os.path.exists(path):
            sys.exit("font not found: {} (this script runs on macOS only, see -h)".format(path))
    from PIL import ImageFont
    return (ImageFont.truetype(MONO_FONT, 15), ImageFont.truetype(SANS_FONT, 64), ImageFont.truetype(SANS_FONT, 30),
            ImageFont.truetype(MONO_FONT, 21))


def make_gif(session, out_path, mono):
    from PIL import Image, ImageDraw

    width, line_h, pad, top = 940, 21, 18, 34
    rows = sum(1 + len(output) for _, output in session)
    height = top + pad * 2 + rows * line_h
    lines = []  # 已经显示的 (颜色, 文本)
    frames, durations = [], []

    def render(cursor_text=None):
        image = Image.new("RGB", (width, height), BG)
        draw = ImageDraw.Draw(image)
        draw.rectangle([0, 0, width, top - 8], fill=TITLE_BAR)
        for i, color in enumerate(((243, 139, 168), (249, 226, 175), (166, 227, 161))):
            draw.ellipse([14 + i * 20, 9, 26 + i * 20, 21], fill=color)
        y = top + pad
        shown = lines + ([cursor_text] if cursor_text else [])
        for color, text in shown:
            if color == PROMPT:
                draw.text((pad, y), "$", font=mono, fill=PROMPT)
                draw.text((pad + 18, y), text, font=mono, fill=FG)
            else:
                draw.text((pad, y), text, font=mono, fill=color)
            y += line_h
        return image

    def add(image, ms):
        frames.append(image)
        durations.append(ms)

    add(render(), 600)
    for command, output in session:
        is_comment = command.startswith("#")
        color = COMMENT if is_comment else PROMPT
        # 逐字出现，每 2 个字符一帧；注释整行出现。
        if not is_comment:
            for end in range(2, len(command) + 2, 2):
                add(render((color, command[:end] + "_")), 45)
        lines.append((color, command))
        add(render(), 500 if not is_comment else 1400)
        if output:
            lines.extend((FG, line) for line in output)
            add(render(), 1800)
    add(render(), 3500)
    # 64 色：32 色时窗口按钮的红黄绿会被量化成灰色。
    palette_frames = [frame.convert("P", palette=Image.ADAPTIVE, colors=64) for frame in frames]
    palette_frames[0].save(out_path, save_all=True, append_images=palette_frames[1:], duration=durations,
                           loop=0, optimize=True, disposal=1)


def fit_mono(mono, lines, max_width):
    """表格最长一行放不进边框时逐号调小等宽字号（左右各留 48 像素，max_width 已扣除）。

    list 简表加列后（如 0.11 的 LAST USED），21 号字会画到边框外；减到 14 号仍放不下就报错退出，
    不生成越界的图——缩略图里字太小同样看不清，那时应该改版式而不是继续缩字。
    """
    from PIL import ImageFont
    size = mono.size
    while size >= 14:
        font = ImageFont.truetype(MONO_FONT, size)
        if max(font.getlength(line) for line in lines) <= max_width:
            return font
        size -= 1
    sys.exit("the list table does not fit in the social preview even at 14 pt; change the layout")


def make_social(session, out_path, mono, title_font, tagline_font):
    """社交预览图：链接被分享到 X、Reddit、Slack 时显示的缩略图。只放表头和账号行，字号要大到缩略图里也看得清。"""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1280, 640), BG)
    draw = ImageDraw.Draw(image)
    draw.text((80, 70), "multi-codex", font=title_font, fill=FG)
    draw.text((82, 155), "Run several Codex CLI accounts side by side", font=tagline_font, fill=COMMENT)
    box = [80, 250, 1200, 500]
    draw.rounded_rectangle(box, radius=14, fill=(24, 24, 37), outline=TITLE_BAR, width=2)
    list_output = next(output for command, output in session if command == "multi-codex list")
    table = [line for line in list_output if line.startswith(("NAME", "work", "personal"))]
    mono = fit_mono(mono, table, box[2] - box[0] - 96)
    y = box[1] + 32
    draw.text((box[0] + 32, y), "$", font=mono, fill=PROMPT)
    draw.text((box[0] + 58, y), "multi-codex list", font=mono, fill=FG)
    y += 16
    for line in table:
        y += 40
        draw.text((box[0] + 32, y), line, font=mono, fill=FG)
    draw.text((82, 545), "github.com/jakoes-wu/multi-codex", font=tagline_font, fill=PROMPT)
    image.save(out_path, optimize=True)


def main():
    parser = argparse.ArgumentParser(
        description="Generate docs/assets/demo.gif and docs/assets/social-preview.png from a real "
                    "multi-codex session in a temporary HOME (macOS, needs Pillow).",
        epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=os.path.join(ROOT, "docs", "assets"),
                        help="output directory (default: docs/assets in this repository)")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="print the captured session and exit without drawing")
    args = parser.parse_args()

    session = capture_session()
    if args.print_only:
        for command, output in session:
            print(command if command.startswith("#") else "$ " + command)
            for line in output:
                print(line)
        return 0
    try:
        import PIL  # noqa: F401
    except ImportError:
        sys.exit("Pillow is required: pip install Pillow")
    mono, title_font, tagline_font, social_mono = load_fonts()
    os.makedirs(args.out, exist_ok=True)
    gif = os.path.join(args.out, "demo.gif")
    png = os.path.join(args.out, "social-preview.png")
    make_gif(session, gif, mono)
    make_social(session, png, social_mono, title_font, tagline_font)
    print("wrote {} ({} KB)".format(gif, os.path.getsize(gif) // 1024))
    print("wrote {} ({} KB)".format(png, os.path.getsize(png) // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())

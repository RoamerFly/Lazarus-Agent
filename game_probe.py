"""Exercise the recognized game in the current workbench job's isolated Android device."""
import hashlib
import re
import time

import cv2
import numpy as np

from bench_device import ADB, invoke, shell
from tools.pacific_game_checkpoints import MATCH_THRESHOLD, ROOT, STAGES, gray, locate


def system_prompt(focus, frame):
    # Both the system-owned window and its displayed dialog must match. The
    # permission reference includes the game name and the local-files request.
    compatibility_notice = re.search(r"\bu\d+ Deprecated(?:Abi|TargetSdkVersion)Dialog\}", focus)
    if re.search(r"\bu\d+ com\.android\.permissioncontroller/[^\s}]*GrantPermissionsActivity\}", focus):
        reference = ROOT / "redroid16-game-ui" / "storage-permission.png"
        bounds, button = (187, 119, 532, 287), (360, 260)
    elif compatibility_notice:
        reference = ROOT / "redroid16-canary-screen.png"
        # These are two distinct framework windows with different explanatory
        # text, but both expose the same acknowledgement button.
        bounds, button = (493, 266, 548, 302), (520, 285)
    else:
        return None
    sample = gray(reference)
    if frame.shape != sample.shape:
        return None
    left, top, right, bottom = bounds
    template = sample[top:bottom, left:right]
    matches = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, (x, y) = cv2.minMaxLoc(matches)
    if score < .94 or (compatibility_notice and x < frame.shape[1] // 2):
        return None
    return x + button[0] - left, y + button[1] - top


def exercise_game(serial, package, directory, log):
    directory.mkdir()
    steps = []
    last_input = 0.0

    def press(point, duration):
        # Unity polls touch state during frames. A down/up pair delivered in
        # the same frame can disappear before a slow software renderer polls.
        shell(serial, "input", "swipe", *point, *point, duration)

    for stage in STAGES:
        deadline = time.monotonic() + (1000 if stage == "intro" else 480)
        while time.monotonic() < deadline:
            windows = shell(serial, "dumpsys", "window", "displays", timeout=60)
            focus = next((line for line in windows.splitlines() if "mCurrentFocus=" in line), "")
            shot = invoke([ADB, "-s", serial, "exec-out", "screencap", "-p"], 60)
            if shot.returncode or not shot.stdout.startswith(b"\x89PNG"):
                raise ValueError("无法读取游戏窗口截图")
            frame = cv2.imdecode(np.frombuffer(shot.stdout, np.uint8), cv2.IMREAD_GRAYSCALE)
            if frame is None:
                raise ValueError("游戏窗口截图无效")
            point = system_prompt(focus, frame)
            if point:
                (directory / ("system-" + str(point[1]) + ".png")).write_bytes(shot.stdout)
                shell(serial, "input", "tap", *point)
            elif re.search(r"\bu\d+ "+re.escape(package)+r"/", focus):
                score, point = locate(frame, stage)
                if score >= MATCH_THRESHOLD:
                    break
                if steps and time.monotonic() - last_input > 20:
                    prior = steps[-1]
                    previous_score, _ = locate(frame, prior["expected"])
                    if previous_score >= MATCH_THRESHOLD and prior.get("retries", 0) < 4:
                        press(prior["point"], 3000 if prior["action"] == "hold" else 700)
                        prior["retries"] = prior.get("retries", 0) + 1
                        last_input = time.monotonic()
                        log(f"仍处于 {prior['expected']} 画面，已重新执行对应操作")
            elif not invoke([ADB, "-s", serial, "shell", "pidof", package], 15).stdout.strip():
                crash = invoke([ADB, "-s", serial, "logcat", "-b", "crash", "-d", "-t", "300"], 30).stdout
                if (f">>> {package} <<<".encode() in crash or f"Process: {package},".encode() in crash):
                    (directory / "crash.txt").write_bytes(crash)
                    raise ValueError(f"目标游戏在 {stage} 检查前崩溃")
            (directory / "last.png").write_bytes(shot.stdout)
            time.sleep(3)
        else:
            raise ValueError(f"未观察到游戏检查点 {stage}，已保留最后画面")
        (directory / (stage + ".png")).write_bytes(shot.stdout)
        if stage == "block":
            rows = np.flatnonzero(frame.max(axis=1) > 8)
            x = frame.shape[1] // 2
            y = round(rows[0] + (rows[-1] - rows[0]) * .875)
            point = (x, y)
            press(point, 3000)
            action = "hold"
        elif stage != "evade":
            press(point, 500)
            action = "tap"
        else:
            action = "observed"
        steps.append(dict(expected=stage, score=round(score, 3), foreground=package,
                          point=list(map(int, point)), action=action,
                          screenshot_sha256=hashlib.sha256(shot.stdout).hexdigest()))
        last_input = time.monotonic()
        log(f"已在目标应用中验证游戏画面 {stage}")
    return {"result":"passed","steps":steps,"evidence_folder":directory.name}

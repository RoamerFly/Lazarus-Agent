"""On-demand CPU translation with the public OPUS-MT English–Chinese model."""

import hashlib
from pathlib import Path
import shutil
import tempfile
from urllib.request import urlopen, build_opener, ProxyHandler, Request
from urllib.error import URLError
import zipfile
import re

from storage_paths import resolve_storage_paths

URL = "https://argos-net.com/v1/translate-en_zh-1_9.argosmodel"
SHA = "433e7c4f034d87fbe2353161e05f18646d7999452f801a4e1f0378522b9850ab"
MEMBERS = (
    "sentencepiece.model",
    "model/config.json",
    "model/model.bin",
    "model/shared_vocabulary.json",
    "README.md",
)


def model_dir(progress):
    root = resolve_storage_paths().models
    root.mkdir(parents=True, exist_ok=True)
    dest = root / ("opus-en-zh-" + SHA[:12])
    if all((dest / name).is_file() for name in MEMBERS):
        return dest
    progress("首次使用，正在下载并校验本机英译中模型（约 67 MiB）")
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        archive = Path(tmp) / "model.zip"
        for opener in (urlopen, build_opener(ProxyHandler({})).open):
            request = Request(URL, headers={"User-Agent": "Lazarus-Agent/0.6"})
            try:
                digest = hashlib.sha256()
                total = 0
                with opener(request, timeout=25) as response, archive.open("wb") as out:
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > 72 * 1024**2:
                            raise ValueError("翻译模型下载超过预期大小")
                        digest.update(chunk)
                        out.write(chunk)
                break
            except (OSError, URLError):
                if opener is not urlopen:
                    raise ValueError("无法下载本机翻译模型，请检查网络后重试")
                progress("系统网络连接未完成，正在直连模型发布站重试")
        if digest.hexdigest() != SHA:
            raise ValueError("下载的英译中模型哈希不匹配")
        stage = Path(tmp) / "ready"
        with zipfile.ZipFile(archive) as data:
            for name in MEMBERS:
                p = stage / name
                p.parent.mkdir(parents=True, exist_ok=True)
                with (
                    data.open("translate-en_zh-1_9/" + name) as src,
                    p.open("wb") as out,
                ):
                    shutil.copyfileobj(src, out)
        try:
            stage.rename(dest)
        except FileExistsError:
            if not all((dest / name).is_file() for name in MEMBERS):
                raise
    return dest


def translate(values, progress, glossary=None):
    from auto_translation import TOKEN
    import ctranslate2
    import sentencepiece as spm
    from opencc import OpenCC

    if not values:
        return {}
    root = model_dir(progress)
    processor = spm.SentencePieceProcessor(
        model_proto=(root / "sentencepiece.model").read_bytes()
    )
    files = {p.name: p.read_bytes() for p in (root / "model").iterdir() if p.is_file()}
    translator = ctranslate2.Translator(
        "opus-en-zh", files=files, device="cpu", compute_type="int8", intra_threads=4
    )
    simplify = OpenCC("t2s")
    # Keep formatting instructions out of the neural decoder entirely.
    reserved = re.compile("(" + TOKEN.pattern + r"|\\n|[\n\t\r]+)")
    split = {s: reserved.split(s) for s in values}
    fragments = list(
        dict.fromkeys(
            p.strip()
            for parts in split.values()
            for p in parts[::2]
            if re.search("[A-Za-z]", p)
        )
    )
    terms = {
        k.casefold(): v
        for k, v in (glossary or {}).items()
        if len(k) > 2 and k.isascii() and "%" not in k
    }
    term_pattern = (
        re.compile(
            r"(?<![A-Za-z])(?:"
            + "|".join(re.escape(k) for k in sorted(terms, key=len, reverse=True))
            + r")(?![A-Za-z])",
            re.I,
        )
        if terms
        else None
    )
    mapped = {}
    for offset in range(0, len(fragments), 32):
        batch = fragments[offset : offset + 32]
        progress(
            f"本机模型正在补译第 {offset + 1} 至 {offset + len(batch)} 段，共 {len(fragments)} 段"
        )
        result = translator.translate_batch(
            processor.encode(batch, out_type=str),
            beam_size=4,
            repetition_penalty=1.15,
            no_repeat_ngram_size=4,
        )
        for source, res in zip(batch, result):
            text = simplify.convert(
                processor.decode(res.hypotheses[0]).replace("▁", " ").strip()
            )
            mapped[source] = (
                term_pattern.sub(lambda m: terms[m[0].casefold()], text)
                if term_pattern
                else text
            )
    return {
        s: "".join(
            p
            if i % 2 or p.strip() not in mapped
            else re.sub(r"\S(?:[\s\S]*\S)?", lambda _: mapped[p.strip()], p, count=1)
            for i, p in enumerate(parts)
        )
        for s, parts in split.items()
    }

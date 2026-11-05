"""File tools confined to one disposable Apktool tree."""
import fnmatch
import hashlib
from pathlib import PurePosixPath
import re
from xml.etree import ElementTree as ET


MAX_TEXT_BYTES = 512 * 1024
MAX_PATCH_BYTES = 256 * 1024
MAX_CHANGES = 64
TEXT_SUFFIXES = {".xml", ".smali", ".json", ".txt", ".csv", ".properties",
                 ".yml", ".yaml", ".js", ".html", ".css", ".ini", ".cfg", ".lua"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def digest_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class Workspace:
    def __init__(self, root):
        self.root = root.resolve(strict=True)
        self.read_hashes = {}
        self.originals = {}
        self.revision = 0

    def path(self, name):
        if (not isinstance(name, str) or not name or len(name) > 400 or
                "\\" in name or ":" in name or "\0" in name):
            raise ValueError("使用 APK 工作区内的相对路径")
        relative = PurePosixPath(name)
        if relative.is_absolute() or any(not part or part.endswith((".", " ")) or
                                        re.search(r'[<>"|?*\x00-\x1f]', part) or
                                        re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", part)
                                        for part in name.split("/")):
            raise ValueError("文件路径越出 APK 工作区")
        path = self.root.joinpath(*relative.parts)
        for item in (path, *path.parents):
            if item == self.root:
                break
            if item.is_symlink():
                raise ValueError("工作区不允许链接或重解析点")
            if item.exists() and getattr(item.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400:
                raise ValueError("工作区不允许链接或重解析点")
        path.resolve(strict=False).relative_to(self.root)
        return path

    def editable(self, name):
        if not isinstance(name, str):
            return False
        parts = PurePosixPath(name).parts
        return (name == "AndroidManifest.xml" or
                bool(parts and (parts[0] in {"res", "assets"} or
                                parts[0] == "smali" or parts[0].startswith("smali_classes")))) and (
                    PurePosixPath(name).suffix.lower() in TEXT_SUFFIXES)

    def _text(self, name):
        path = self.path(name)
        if not path.is_file() or path.stat().st_size > MAX_TEXT_BYTES:
            raise ValueError("文件不存在或超过文本读取上限")
        raw = path.read_bytes()
        if b"\0" in raw:
            raise ValueError("该文件是二进制资源")
        return raw, raw.decode("utf-8")

    def list_files(self, pattern="*", offset=0):
        self._query(pattern, offset)
        paths = sorted(path.relative_to(self.root).as_posix()
                       for path in self.root.rglob("*") if path.is_file())
        matches = [name for name in paths if fnmatch.fnmatchcase(name, pattern)]
        return {"total": len(matches), "files": [
            {"path": name, "size": self.path(name).stat().st_size, "editable": self.editable(name)}
            for name in matches[offset:offset+80]], "next_offset": offset+80 if len(matches)>offset+80 else None}

    def read_text(self, path, start_line=1, line_count=100):
        if type(start_line) is not int or start_line < 1 or type(line_count) is not int or not 1 <= line_count <= 180:
            raise ValueError("读取范围无效")
        raw, text = self._text(path)
        sha = digest(raw)
        self.read_hashes[path] = sha
        lines = text.splitlines()
        selected = lines[start_line-1:start_line-1+line_count]
        content = "\n".join(f"{start_line+i}: {line}" for i,line in enumerate(selected))
        return {"path": path, "sha256": sha, "total_lines": len(lines),
                "content": content[:16000], "truncated": len(content)>16000}

    def search_text(self, query, pattern="*", offset=0):
        self._query(pattern, offset)
        if not isinstance(query, str) or not 1 <= len(query) <= 200:
            raise ValueError("请输入 1 至 200 字的搜索文本")
        hits = []
        scanned = 0
        for file in sorted(self.root.rglob("*")):
            name = file.relative_to(self.root).as_posix()
            if not file.is_file() or not fnmatch.fnmatchcase(name, pattern) or not self.editable(name):
                continue
            scanned += 1
            if scanned > 12000:
                break
            try:
                _, text = self._text(name)
            except (ValueError, UnicodeError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if query.casefold() in line.casefold():
                    hits.append({"path": name, "line": number, "text": line[:350]})
                    if len(hits) >= offset+31:
                        return {"hits": hits[offset:offset+30], "next_offset": offset+30, "scan_limited": False}
        return {"hits": hits[offset:offset+30], "next_offset": None, "scan_limited": scanned>12000}

    def read_method(self, path, method):
        if not isinstance(path, str) or not path.endswith(".smali") or not isinstance(method, str) or not 1 <= len(method) <= 250:
            raise ValueError("请提供 smali 路径及方法名或完整签名")
        raw, text = self._text(path)
        matches = []
        for match in re.finditer(r"(?m)^\.method ([^\r\n]+)\r?\n[\s\S]*?^\.end method", text):
            signature = match[1].split()[-1]
            if method == signature or method == signature.split("(")[0]:
                matches.append(match)
        if len(matches) != 1:
            raise ValueError("方法必须唯一匹配，重载方法请使用完整签名")
        match = matches[0]
        if len(match[0]) > 15000:
            return {"start_line": text[:match.start()].count("\n")+1,
                    "message": "方法较长，请用 read_text 分段读取"}
        sha = digest(raw)
        self.read_hashes[path] = sha
        return {"path": path, "sha256": sha, "signature": match[1],
                "content": match[0], "start_line": text[:match.start()].count("\n")+1}

    @staticmethod
    def _query(pattern, offset):
        if not isinstance(pattern, str) or not 1 <= len(pattern) <= 200 or type(offset) is not int or not 0 <= offset <= 20000:
            raise ValueError("搜索范围无效")

    def replace_text(self, path, sha256, old, new):
        if not self.editable(path):
            raise ValueError("该文件不属于可编辑的 APK 代码或资源")
        raw, text = self._text(path)
        current = digest(raw)
        if self.read_hashes.get(path) != current or sha256 != current:
            raise ValueError("请先读取当前文件并使用返回的 SHA-256")
        if (not isinstance(old, str) or not old or not isinstance(new, str) or old == new or
                len(old.encode("utf-8"))+len(new.encode("utf-8")) > MAX_PATCH_BYTES or "\0" in new):
            raise ValueError("替换内容无效或过长")
        # The model normally copies LF text from a JSON response. Apktool
        # commonly emits CRLF on Windows. Match using the file's separator.
        newline = "\r\n" if "\r\n" in text else "\n"
        old = old.replace("\r\n", "\n").replace("\n", newline)
        new = new.replace("\r\n", "\n").replace("\n", newline)
        if text.count(old) != 1:
            raise ValueError("原文必须唯一匹配，请补充上下文后重试")
        return self._write(path, raw, text.replace(old, new, 1))

    def replace_lines(self, path, sha256, start_line, end_line, content):
        if not self.editable(path) or not isinstance(content, str) or "\0" in content:
            raise ValueError("修改内容或文件类型无效")
        if len(content.encode("utf-8")) > MAX_PATCH_BYTES:
            raise ValueError("修改内容过长")
        raw, text = self._text(path)
        current = digest(raw)
        if self.read_hashes.get(path) != current or sha256 != current:
            raise ValueError("请先读取当前文件并使用返回的 SHA-256")
        lines = text.splitlines(keepends=True)
        if (type(start_line) is not int or type(end_line) is not int or
                not 1 <= start_line <= end_line <= len(lines) or end_line-start_line >= 180):
            raise ValueError("替换范围无效")
        newline = "\r\n" if "\r\n" in text else "\n"
        updated = content.replace("\r\n", "\n").rstrip("\n").replace("\n", newline)
        if updated and lines[end_line-1].endswith("\n"):
            updated += newline
        return self._write(path, raw, "".join(lines[:start_line-1]) + updated + "".join(lines[end_line:]))

    def declare_platform_library(self, name):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,99}", name):
            raise ValueError("系统共享库名称无效")
        path = "AndroidManifest.xml"
        raw, text = self._text(path)
        android_name = "{http://schemas.android.com/apk/res/android}name"
        try:
            application = ET.fromstring(text).find("application")
        except ET.ParseError as exc:
            raise ValueError("AndroidManifest.xml 不是有效的 XML") from exc
        if application is None:
            raise ValueError("清单缺少 application 元素")
        if any(node.get(android_name) == name for node in application.findall("uses-library")):
            return {"path": path, "library": name, "changed": False, "revision": self.revision}
        endings = list(re.finditer(r"</application\s*>", text))
        if len(endings) != 1:
            raise ValueError("无法唯一定位 application 结束位置")
        closing = endings[0].start()
        line_start = text.rfind("\n", 0, closing) + 1
        indent = text[line_start:closing]
        declaration = f'<uses-library android:name="{name}" android:required="false"/>'
        if not indent.strip():
            newline = "\r\n" if "\r\n" in text else "\n"
            closing = line_start
            declaration = indent + "    " + declaration + newline
        updated = text[:closing] + declaration + text[closing:]
        try:
            parsed = ET.fromstring(updated).findall("./application/uses-library")
        except ET.ParseError as exc:
            raise ValueError("无法安全添加系统库声明") from exc
        if sum(node.get(android_name) == name for node in parsed) != 1:
            raise ValueError("新增库声明未出现在 application 内")
        return {**self._write(path, raw, updated), "library": name, "changed": True}

    def _write(self, path, raw, text):
        updated = text.encode("utf-8")
        if updated == raw:
            raise ValueError("替换后内容没有变化")
        if len(updated) > MAX_TEXT_BYTES:
            raise ValueError("修改后文件超过大小上限")
        if path not in self.originals and len(self.originals) >= MAX_CHANGES:
            raise ValueError("本轮已达到可修改文件数量上限")
        self.originals.setdefault(path, raw)
        self.path(path).write_bytes(updated)
        self.revision += 1
        self.read_hashes.pop(path, None)
        return {"path": path, "sha256": digest(updated), "revision": self.revision}

    def create_text_file(self, path, content):
        if not self.editable(path) or not isinstance(content, str) or "\0" in content:
            raise ValueError("只能创建 APK 内的文本代码或资源")
        raw = content.encode("utf-8")
        if not raw or len(raw) > MAX_PATCH_BYTES:
            raise ValueError("新文件内容为空或超过上限")
        target = self.path(path)
        if target.exists():
            raise ValueError("文件已存在，请使用精确替换工具")
        if len(self.originals) >= MAX_CHANGES:
            raise ValueError("本轮已达到可修改文件数量上限")
        self.originals.setdefault(path, None)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        self.revision += 1
        return {"path": path, "sha256": digest(raw), "revision": self.revision}

    def restore_file(self, path):
        if path not in self.originals:
            raise ValueError("该文件在本轮没有修改")
        original = self.originals[path]
        target = self.path(path)
        if original is None:
            target.unlink()
        else:
            target.write_bytes(original)
        del self.originals[path]
        self.read_hashes.pop(path, None)
        self.revision += 1
        return {"restored": path, "revision": self.revision}

    def changes(self):
        result = []
        for name, before in self.originals.items():
            after = self.path(name).read_bytes()
            if after != before:
                result.append({"path": name, "before_sha256": digest(before) if before is not None else None,
                               "after_sha256": digest(after)})
        return result

    def copy_changes(self, destination):
        changes = self.changes()
        if not changes:
            raise ValueError("工作区没有修改")
        if sum(self.path(row["path"]).stat().st_size for row in changes) > 4 * 1024 * 1024:
            raise ValueError("本轮修改总量超过上限")
        for row in changes:
            name = row["path"]
            target = destination / name
            if row["before_sha256"] is None:
                if target.exists():
                    raise ValueError("新文件与回编树冲突")
            elif not target.is_file() or digest(target.read_bytes()) != row["before_sha256"]:
                raise ValueError("回编文件与已分析的输入不一致")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.path(name).read_bytes())
        return {"kind": "agent-patch", "files": changes}

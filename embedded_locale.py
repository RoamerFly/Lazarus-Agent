"""Reuse compiled bilingual constructor rows with explicitly named language parameters."""

import json
import re

# Restrict to scalar integers and strings, where each argument occupies one register.
CTOR = re.compile(
    r"^\.method [^\n]* constructor <init>\(((?:I|Ljava/lang/String;)+)\)V\n(.*?)^\.end method",
    re.M | re.S,
)
OWNER = re.compile(r"^\.class [^\n]* (L[^;\n]+;)$", re.M)
PARAM = re.compile(r'^\s*\.param p(\d+), "([^"]+)"', re.M)
BLOCK = re.compile(
    r"^    new-instance (v\d+), (L[^;\n]+;)\n((?:[ \t]*\n|    (?:const[^\n]*|move(?:/\w+)? v\d+, v\d+)\n){0,96})(    invoke-direct(?:/range)? \{([^}]+)\}, (L[^;\n]+;)-><init>\(([^)]+)\)V)$",
    re.M,
)
LITERAL = re.compile(r'^    const-string(?:/jumbo)? (v\d+), ("(?:\\.|[^"\\])*")$', re.M)


def reuse(decoded):
    paths = [
        p
        for smali in decoded.glob("smali*")
        if smali.is_dir()
        for p in smali.rglob("*.smali")
    ]
    types = {}
    for path in paths:
        data = path.read_text(encoding="utf8")
        if '"zh_cn"' not in data and '"zh_CN"' not in data:
            continue
        owner = OWNER.search(data)
        if not owner:
            continue
        for match in CTOR.finditer(data):
            params = {name.lower(): int(i) for i, name in PARAM.findall(match[2])}
            if "en_us" in params and "zh_cn" in params:
                types[owner[1], match[1]] = params
    terms = {}
    conflicts = set()
    changed = 0
    if not types:
        return {}, 0
    from auto_translation import TOKEN

    for path in paths:
        data = path.read_text(encoding="utf8")
        if "invoke-direct" not in data or "const-string" not in data:
            continue

        def replace(match):
            nonlocal changed
            obj, typ, block, call, args, called, signature = match.groups()
            params = types.get((typ, signature))
            if not params or called != typ:
                return match[0]
            if " .. " in args:
                try:
                    first, last = (int(s.removeprefix("v")) for s in args.split(" .. "))
                except ValueError:
                    return match[0]
                if last - first > 32:
                    return match[0]
                regs = [f"v{i}" for i in range(first, last + 1)]
            else:
                regs = args.split(", ")
            if regs[0] != obj or len(regs) <= max(params.values()):
                return match[0]
            literals = {}
            for line in block.splitlines():
                if m := LITERAL.fullmatch(line):
                    literals[m[1]] = m[2]
                elif m := re.match(r"    (?:const|move)\S* (v\d+),", line):
                    literals.pop(m[1], None)
            try:
                zh = json.loads(literals[regs[params["zh_cn"]]])
            except (ValueError, KeyError):
                return match[0]
            if not re.search("[一-鿿]", zh):
                return match[0]
            before, after = [], []
            for lang in ("en_us", "plural"):
                if lang not in params:
                    continue
                reg = regs[params[lang]]
                if reg not in literals or regs.count(reg) != 1:
                    continue
                try:
                    en = json.loads(literals[reg])
                except ValueError:
                    continue
                if not re.search("[A-Za-z]", en) or TOKEN.findall(en) != TOKEN.findall(
                    zh
                ):
                    continue
                before.append(f"    const-string/jumbo {reg}, {json.dumps(zh)}")
                after.append(f"    const-string/jumbo {reg}, {literals[reg]}")
                if en in terms and terms[en] != zh:
                    conflicts.add(en)
                else:
                    terms[en] = zh
                changed += 1
            if not before:
                return match[0]
            return match[0][: -len(call)] + "\n".join([*before, call, *after])

        updated = BLOCK.sub(replace, data)
        if updated != data:
            path.write_text(updated, encoding="utf8")
    return {k: v for k, v in terms.items() if k not in conflicts}, changed

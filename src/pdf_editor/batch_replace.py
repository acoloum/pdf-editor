"""批次文字取代：在整份文件中找出內容與規則「整格完全相同」的文字，逐筆以單筆修改相同的方式取代。
選用「部分相符」時，句子中包含規則文字的段落也會取代其中相符的部分。

每筆取代都沿用手動修改文字時的設定：優先使用原字型（缺字時改用相近字型或內建中文字型），
表格內的文字放回儲存格正中央並在放不下時縮小字級，置中的標題維持置中。"""
import hashlib
import re
from dataclasses import dataclass

import pymupdf

from pdf_editor.engine.fonts import checked_font, default_font, embedded_font, similar_font
from pdf_editor.engine.text import extract_runs, find_table_cell, replace_text
from pdf_editor.errors import EditorError
from pdf_editor.model import TextReplacement, TextRun

MAX_RULES = 100
MAX_CANDIDATES = 2000


@dataclass(frozen=True)
class ReplaceRule:
    find: str
    replace: str


@dataclass(frozen=True)
class ReplaceCandidate:
    """找到的一筆：所在頁、原本的文字區段與要換成的內容（空字串代表刪除）。"""
    page: int
    run: TextRun
    new_text: str


@dataclass(frozen=True)
class ReplaceFailure:
    candidate: ReplaceCandidate
    message: str


def match_key(text):
    """比對用的內容：忽略空白（PDF 常在中文字之間或頭尾多出空格）。"""
    return "".join(str(text).split())


def validated_rules(rules):
    """去掉空白規則；尋找內容重複或與取代內容相同時說明原因。"""
    result = {}
    for rule in rules:
        find, replace = str(rule.find).strip(), str(rule.replace).strip()
        if not find and not replace:
            continue
        if not match_key(find):
            raise EditorError("REPLACE_RULE", f"「{replace}」缺少要尋找的文字。")
        key = match_key(find)
        if key in result:
            raise EditorError("REPLACE_RULE", f"「{find}」重複出現在多條規則中，請只保留一條。")
        if key == match_key(replace):
            raise EditorError("REPLACE_RULE", f"「{find}」的取代內容與原文字相同。")
        result[key] = ReplaceRule(find, replace)
    if not result:
        raise EditorError("REPLACE_RULE", "請至少輸入一條取代規則。")
    if len(result) > MAX_RULES:
        raise EditorError("REPLACE_RULE", f"取代規則最多 {MAX_RULES} 條。")
    return result


def _partial_replacer(lookup):
    """部分相符：同一段文字中所有規則一次換掉，不連鎖取代；重疊時較長的「尋找」優先。"""
    rules = {rule.find: rule.replace for rule in lookup.values()}
    pattern = re.compile("|".join(re.escape(find)
        for find in sorted(rules, key=len, reverse=True)))
    return lambda text: pattern.sub(lambda match: rules[match.group(0)], text)


def find_candidates(pdf, rules, partial=False):
    """依頁面順序列出內容與某條規則整格相同的文字；無法安全修改的文字也列出，由介面標示略過。

    partial 為 True 時，內容「包含」規則文字的段落也列入，只換掉其中相符的部分。
    """
    lookup = validated_rules(rules)
    replacer = _partial_replacer(lookup) if partial else None
    found = []
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        page_count = doc.page_count
    for page in range(page_count):
        for run in extract_runs(pdf, page):
            rule = lookup.get(match_key(run.text))
            if rule is not None:
                new_text = rule.replace
            elif replacer is not None:
                new_text = replacer(run.text).strip()
                if new_text == run.text.strip():
                    continue
            else:
                continue
            found.append(ReplaceCandidate(page, run, new_text))
            if len(found) > MAX_CANDIDATES:
                raise EditorError("REPLACE_RULE",
                    f"符合的文字超過 {MAX_CANDIDATES} 筆，請改用更明確的規則。")
    return tuple(found)


def _is_page_centered(rect, page_width, tolerance=3.0):
    """與手動修改相同：中心落在頁面中線附近、且不是整行寬的文字，視為置中標題。"""
    center = (rect[0] + rect[2]) / 2
    return abs(center - page_width / 2) <= tolerance and rect[2] - rect[0] <= page_width * 0.9


def _font_for(pdf, candidate, folder):
    run, text = candidate.run, candidate.new_text
    original = embedded_font(pdf, candidate.page, run, folder)
    if original:
        font_path = str(original)
    else:
        similar = similar_font(run.font_name, run.text)
        font_path = similar[1] if similar else str(default_font())
    if not text:
        return font_path
    try:
        checked_font(font_path, text)
    except EditorError as exc:
        if exc.code != "FONT_MISSING_GLYPH":
            raise
        similar = similar_font(run.font_name, text)
        font_path = similar[1] if similar else str(default_font())
    return font_path


def _planned_request(pdf, candidate, folder, key, page_width):
    """以原始文件決定文字框、對齊與字型；送出前再換上當下的文件雜湊與文字代碼。"""
    run = candidate.run
    cell = find_table_cell(pdf, candidate.page, run.rect, key)
    if cell:
        rect, alignment, fit = cell, "center", "shrink"
    elif _is_page_centered(run.rect, page_width):
        rect, alignment, fit = run.rect, "hcenter", "expand"
    else:
        rect, alignment, fit = run.rect, "left", "expand"
    return TextReplacement("", candidate.page, run.id, candidate.new_text, tuple(rect),
        _font_for(pdf, candidate, folder), run.size, run.color, alignment, run.bold, fit)


def _same_place(first, second, tolerance=0.5):
    return all(abs(a - b) <= tolerance for a, b in zip(first, second))


def apply_replacements(pdf, candidates, font_folder):
    """逐筆取代，回傳 (新文件, 成功筆數, 失敗清單)；個別失敗（例如放不下、與其他文字重疊）
    不影響其他筆，整批只需寫入一次歷程。"""
    key = hashlib.sha256(pdf).hexdigest()
    widths = {}
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        for candidate in candidates:
            if candidate.page not in widths:
                widths[candidate.page] = doc[candidate.page].cropbox.width
    plans = []
    failures = []
    for candidate in candidates:
        try:
            plans.append((candidate, _planned_request(pdf, candidate, font_folder, key,
                widths[candidate.page])))
        except EditorError as exc:
            failures.append(ReplaceFailure(candidate, str(exc)))
    current = pdf
    applied = 0
    for candidate, plan in plans:
        # 同頁先取代的文字會改變其他文字的代碼：以內容與位置在當下的文件中找回同一段。
        run = next((item for item in extract_runs(current, candidate.page)
            if item.text == candidate.run.text and _same_place(item.rect, candidate.run.rect)),
            None)
        if run is None:
            failures.append(ReplaceFailure(candidate, "找不到這段文字，可能已被其他取代影響。"))
            continue
        request = TextReplacement(hashlib.sha256(current).hexdigest(), plan.page, run.id,
            plan.text, plan.rect, plan.font_path, plan.size, plan.color, plan.alignment,
            plan.bold, plan.fit)
        try:
            current = replace_text(current, request)
        except EditorError as exc:
            failures.append(ReplaceFailure(candidate, str(exc)))
            continue
        applied += 1
    return current, applied, tuple(failures)

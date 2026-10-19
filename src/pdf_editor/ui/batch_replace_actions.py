"""批次取代：開啟對話框、記住上次的規則，並在背景一次套用；整批只留一筆可復原的紀錄。"""
import json

from PySide6.QtWidgets import QMessageBox

from pdf_editor.batch_replace import apply_replacements
from pdf_editor.ui.batch_replace_dialog import BatchReplaceDialog

RULES_KEY="batch_replace/rules"
PARTIAL_KEY="batch_replace/partial"
# 失敗清單太長時只列前幾筆，其餘以筆數帶過。
MAX_LISTED_FAILURES=10


class BatchReplaceActionsMixin:
    def saved_replace_rules(self):
        try:
            rules=json.loads(str(self.settings.value(RULES_KEY,"") or "[]"))
        except ValueError:
            return []
        # 每條為（尋找, 取代為, 同列條件）；舊版只存兩欄，同列條件補空白。
        return [tuple(rule)+("",)*(3-len(rule)) for rule in rules
            if isinstance(rule,list) and len(rule) in (2,3)
            and all(isinstance(value,str) for value in rule)]

    def show_batch_replace_dialog(self):
        if not self.session or self.busy:
            return
        if not self.session.access.can_edit:
            self.statusBar().showMessage(self.session.access.reason or "文件僅供閱讀，無法修改。")
            return
        self.cancel_editing_modes(crop=False)
        rules=self.saved_replace_rules()
        # 先點選一格文字再開啟時，直接把它帶入成新規則，只要再輸入要換成的內容。
        run=self.run
        if run is not None and run.editable and all(rule[0]!=run.text.strip() for rule in rules):
            rules.append((run.text.strip(),"",""))
        partial=str(self.settings.value(PARTIAL_KEY,"false")).lower()=="true"
        dialog=BatchReplaceDialog(self.session.pdf,rules,self,partial)
        if run is not None and run.editable:
            row=next(row for row,rule in enumerate(dialog.rule_rows()) if rule[0]==run.text.strip())
            dialog.rules.setCurrentCell(row,1)
        dialog.page_requested.connect(self.goto_page)
        accepted=dialog.exec()
        self.settings.set_value(RULES_KEY,json.dumps(dialog.rule_rows(),ensure_ascii=False))
        self.settings.set_value(PARTIAL_KEY,"true" if dialog.partial.isChecked() else "false")
        self.settings.sync()
        if accepted:
            self.apply_batch_replace(dialog.selected_candidates())

    def apply_batch_replace(self,candidates):
        if not self.session or self.busy or not candidates:
            return
        revision,token=self.session.revision,self.token
        pages=tuple(sorted({candidate.page for candidate in candidates}))
        self.clear_selection_state(disable_panel=True)
        self.busy=True
        self.refresh_actions()
        self.statusBar().showMessage(f"正在取代 {len(candidates)} 筆文字…")

        def done(result):
            self.busy=False
            if self.closed or token!=self.token or revision!=self.session.revision:
                self.refresh_actions()
                return
            pdf,applied,failures=result
            if applied:
                self.session.apply_pdf(pdf)
            status=f"已取代 {applied} 筆文字。" if applied else "沒有取代任何文字。"
            if failures:
                status+=f"{len(failures)} 筆未套用。"
            self.refresh_actions()
            self.request_render(status)
            if applied:
                self.queue_thumbnails(pages)
            if failures:
                self.show_replace_failures(applied,failures)

        self.jobs.submit(apply_replacements,
            (self.session.pdf,tuple(candidates),str(self.session.history.root)),done,self.error)

    def show_replace_failures(self,applied,failures):
        lines=[f"第 {item.candidate.page+1} 頁「{item.candidate.run.text.strip()}」：{item.message}"
            for item in failures[:MAX_LISTED_FAILURES]]
        if len(failures)>MAX_LISTED_FAILURES:
            lines.append(f"……另有 {len(failures)-MAX_LISTED_FAILURES} 筆")
        head=(f"已取代 {applied} 筆，下列 {len(failures)} 筆未套用（可按 Ctrl+Z 整批還原）："
            if applied else f"下列 {len(failures)} 筆未能取代：")
        QMessageBox.warning(self,"部分文字未取代",head+"\n\n"+"\n".join(lines))

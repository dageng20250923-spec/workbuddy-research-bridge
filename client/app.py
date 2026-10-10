"""User-opened native configuration window. No tray, polling or background worker."""
import argparse
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog
from tkinter.scrolledtext import ScrolledText

from core import Workspace, ClientError, defaults, validate_config, check_paths

PAPER = '#f4f2eb'
INK = '#233d34'
MUTED = '#64736a'
FIELDS = (('zotero_data','Zotero 数据目录'), ('obsidian_vault','Obsidian 库目录'),
          ('download_dir','下载目录（规划）'), ('scansci_skill','scansci-pdf 技能目录'),
          ('scansci_python','ScanSci Python（只检查文件）'))
STATUS_TEXT = {
    'not_selected':'未选择', 'missing':'路径不存在', 'not_accessible':'路径不可访问',
    'files_found_not_running_verified':'文件存在；未验证运行状态',
    'files_found_not_enabled_verified':'插件文件存在；未验证启用状态',
    'zotero_marker_missing':'未找到 zotero.sqlite 标记',
    'obsidian_marker_missing':'未找到 .obsidian 库标记',
    'skill_marker_missing':'未找到 SKILL.md', 'manifest_missing':'未找到 Research Bridge',
    'manifest_unreadable':'Research Bridge 标识不可确认',
    'simulation_downloaded':'模拟已下载', 'simulation_not_downloaded':'未下载（模拟）',
    'simulation_imported':'模拟已入库', 'simulation_import_pending':'模拟待入库',
    'simulation_note_ready':'模拟卡片就绪', 'not_started':'未开始',
}


class PaperFlowApp:
    def __init__(self, root, workspace=None):
        self.root = root
        self.client_dir = Path(__file__).resolve().parent
        root.title('PaperFlow · 文献整理中心（本地验证版）')
        root.configure(background=PAPER)
        root.geometry('1120x850')
        root.minsize(980, 720)
        self.work_var = tk.StringVar(value=str(workspace or self.client_dir / 'runtime'))
        self.vars = {key:tk.StringVar(value=defaults()[key]) for key, _ in FIELDS}
        self.status = tk.StringVar(value='就绪。选择路径不会操作文库；关闭窗口后没有常驻后台。')
        self.summary = tk.StringVar(value='还没有模拟任务。真实 PDF 下载 0 · 真实文库写入 0')
        self.last_result = None
        self.sim_result = {'tasks':[]}
        self._styles()
        self._build()
        self.load_config()

    def _styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei',10), background=PAPER, foreground=INK)
        style.configure('TFrame', background=PAPER)
        style.configure('TLabel', background=PAPER, foreground=INK)
        style.configure('Muted.TLabel', foreground=MUTED)
        style.configure('Title.TLabel', font=('Georgia',28,'bold'), foreground=INK)
        style.configure('Heading.TLabel', font=('Microsoft YaHei',16,'bold'))
        style.configure('TButton', padding=(15,9), background='#e6e9df', borderwidth=0)
        style.map('TButton',background=[('active','#d7dfd1')])
        style.configure('Accent.TButton', background=INK, foreground='#ffffff')
        style.map('Accent.TButton',background=[('active','#3a5a48')])
        style.configure('TNotebook', background=PAPER, borderwidth=0)
        style.configure('TNotebook.Tab',padding=(23,10), background='#e8e6dd')
        style.map('TNotebook.Tab',background=[('selected',PAPER)])
        style.configure('Treeview', rowheight=39, font=('Microsoft YaHei',10),
                        background='#fffef9', fieldbackground='#fffef9', borderwidth=0)
        style.configure('Treeview.Heading', font=('Microsoft YaHei',10,'bold'),padding=(6,9))
        style.map('Treeview',background=[('selected','#dbe7da')],foreground=[('selected',INK)])

    def _build(self):
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill='both', expand=True)
        header = ttk.Frame(outer)
        header.pack(fill='x', pady=(0,16))
        ttk.Label(header,text='PaperFlow',style='Title.TLabel').pack(side='left')
        ttk.Label(header,text='文献整理中心\nLOCAL / 0.2',style='Muted.TLabel').pack(side='left',padx=18)
        ttk.Label(header,text='本地模拟 · 真实整理未启用',foreground='#986434').pack(side='right')
        ttk.Separator(outer).pack(fill='x')
        ttk.Label(outer,text='此阶段无需微信、服务号或服务器。模拟结果不会进入 Zotero / Obsidian。',
                  style='Muted.TLabel').pack(anchor='w',pady=(12,17))
        footer = ttk.Frame(outer)
        footer.pack(side='bottom',fill='x')
        ttk.Separator(footer).pack(fill='x',pady=(10,10))
        self.status_label = ttk.Label(footer,textvariable=self.status,wraplength=1030,style='Muted.TLabel')
        self.status_label.pack(fill='x')
        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill='both',expand=True)
        self.settings = ttk.Frame(self.notebook,padding=(12,20))
        self.tasks = ttk.Frame(self.notebook,padding=(12,20))
        self.integration = ttk.Frame(self.notebook,padding=(12,20))
        for page,label in ((self.settings,'01  本机配置'),(self.tasks,'02  模拟任务'),(self.integration,'03  WorkBuddy 接入')):
            self.notebook.add(page,text=label)
        self._settings_page()
        self._tasks_page()
        self._integration_page()

    def _settings_page(self):
        page = self.settings
        page.columnconfigure(1,weight=1)
        ttk.Label(page,text='先连接你的本机路径',style='Heading.TLabel').grid(row=0,column=0,columnspan=3,sticky='w')
        ttk.Label(page,text='只保存本工具的配置，不修改插件设置，不创建下载目录。',style='Muted.TLabel').grid(
            row=1,column=0,columnspan=3,sticky='w',pady=(8,16))
        ttk.Label(page,text='独立工作目录（D 盘）').grid(row=2,column=0,sticky='w',padx=(0,15))
        ttk.Entry(page,textvariable=self.work_var).grid(row=2,column=1,sticky='ew')
        ttk.Button(page,text='选择…',command=lambda:self.browse_workspace()).grid(row=2,column=2,padx=(12,0))
        for row,(field,label) in enumerate(FIELDS,start=3):
            ttk.Label(page,text=label).grid(row=row,column=0,sticky='w',pady=8,padx=(0,15))
            ttk.Entry(page,textvariable=self.vars[field]).grid(row=row,column=1,sticky='ew',pady=8)
            ttk.Button(page,text='选择…',command=lambda name=field:self.browse(name)).grid(row=row,column=2,padx=(12,0))
        controls = ttk.Frame(page)
        controls.grid(row=8,column=0,columnspan=3,sticky='w',pady=(13,12))
        self.save_button = ttk.Button(controls,text='保存本工具配置',style='Accent.TButton',command=self.save_config)
        self.save_button.pack(side='left',padx=(0,10))
        ttk.Button(controls,text='只读准备检查',command=self.inspect).pack(side='left',padx=(0,10))
        ttk.Button(controls,text='加载已保存配置',command=self.load_config).pack(side='left')
        self.check_text = ScrolledText(page,height=7,wrap='word',font=('Microsoft YaHei',10),
                                       background='#fffef9',foreground=INK,relief='flat',padx=12,pady=8)
        self.check_text.grid(row=9,column=0,columnspan=3,sticky='nsew')
        page.rowconfigure(9,weight=1)
        self._text(self.check_text,'成像方法分类 · 附件副本年份-题名命名 · 不回写旧分类\n'
                   '找到插件文件不代表已启用，当前不检查网络或运行状态。\n'
                   '正式登录、电脑绑定、后台整理均未接入；不需要填写任何密钥。')

    def _tasks_page(self):
        page = self.tasks
        page.columnconfigure(0,weight=1)
        page.rowconfigure(4,weight=1)
        ttk.Label(page,text='验证一条完整的整理链',style='Heading.TLabel').grid(row=0,column=0,sticky='w')
        ttk.Label(page,text='示例覆盖：顺利整理 / 无全文访问 / Zotero 离线 / AI 暂停 / 分类待复核。',
                  style='Muted.TLabel').grid(row=1,column=0,sticky='w',pady=(8,12))
        buttons = ttk.Frame(page)
        buttons.grid(row=2,column=0,sticky='ew',pady=(0,10))
        self.simulate_button = ttk.Button(buttons,text='运行一次模拟',style='Accent.TButton',command=self.simulate)
        self.simulate_button.pack(side='left',padx=(0,10))
        ttk.Button(buttons,text='读取模拟结果',command=self.refresh_report).pack(side='left')
        ttk.Label(page,textvariable=self.summary,wraplength=980).grid(row=3,column=0,sticky='w',pady=(0,12))
        table = ttk.Frame(page)
        table.grid(row=4,column=0,sticky='nsew')
        columns = ('title','category','download','library','notes')
        self.tree = ttk.Treeview(table,columns=columns,show='headings',height=6)
        widths = (325,120,135,135,135)
        for column,label,width in zip(columns,('模拟文献','成像方法','下载','Zotero','文献卡'),widths):
            self.tree.heading(column,text=label)
            self.tree.column(column,width=width,minwidth=75,stretch=column=='title')
        self.tree.grid(row=0,column=0,sticky='nsew')
        scroll = ttk.Scrollbar(table,orient='vertical',command=self.tree.yview)
        scroll.grid(row=0,column=1,sticky='ns')
        horizontal = ttk.Scrollbar(table,orient='horizontal',command=self.tree.xview)
        horizontal.grid(row=1,column=0,sticky='ew')
        self.tree.configure(yscrollcommand=scroll.set,xscrollcommand=horizontal.set)
        table.columnconfigure(0,weight=1)
        table.rowconfigure(0,weight=1)
        self.tree.tag_configure('blocked',foreground='#8b5f31')
        self.tree.bind('<<TreeviewSelect>>',self.show_task)
        self.details = ScrolledText(page,height=5,wrap='word',font=('Microsoft YaHei',10),
                                   background='#fffef9',foreground=INK,relief='flat',padx=12,pady=10)
        self.details.grid(row=5,column=0,sticky='ew',pady=(12,0))
        self._text(self.details,'选择一篇模拟文献，查看失败原因、建议和各阶段记录。\n模拟数据使用 SIM-* 标识，不是真实 DOI；没有生成 PDF。')

    def _integration_page(self):
        page = self.integration
        ttk.Label(page,text='把机械工作交给 WorkBuddy',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(page,text='提供可执行 CLI 和明确的任务契约；不会替你启用定时任务。',style='Muted.TLabel').pack(
            anchor='w',pady=(8,18))
        instructions = ('1  导入随包提供的 paperflow-workbuddy 技能。\n\n'
                        '2  点击下方按钮，生成本机模拟任务文件和参数数组契约。\n\n'
                        '3  把 workbuddy-task.md 交给 WorkBuddy，按契约执行只读检查、一次模拟和结果报告。\n\n'
                        '4  核对逐篇未下载、待入库、AI 暂停；所有结果必须标注模拟。\n\n'
                        '未来真实下载只调用 scansci-pdf；服务/绑定/合法来源门禁未就绪前保持关闭。\n'
                        '本工具不保存账号、Token、Cookie 或 AI Key，也不自动启动 WorkBuddy。')
        ttk.Label(page,text=instructions,wraplength=940,justify='left').pack(anchor='w')
        self.export_button = ttk.Button(page,text='导出 WorkBuddy 模拟任务',style='Accent.TButton',command=self.export_workbuddy)
        self.export_button.pack(anchor='w',pady=(22,14))
        self.export_text = ScrolledText(page,height=6,wrap='word',font=('Microsoft YaHei',10),
                                       background='#fffef9',foreground=INK,relief='flat',padx=12,pady=10)
        self.export_text.pack(fill='both',expand=True)
        self._text(self.export_text,'还未导出。导出仅写入上方选择的独立工作目录，不修改 WorkBuddy 设置。')

    def _text(self,widget,text):
        widget.configure(state='normal')
        widget.delete('1.0','end')
        widget.insert('1.0',text)
        widget.configure(state='disabled')

    def _cfg(self):
        cfg = defaults()
        cfg.update({field:variable.get().strip() for field,variable in self.vars.items()})
        return validate_config(cfg)

    def _action(self, action, success):
        try:
            result = action()
            self.last_result = result
            self.status.set(success)
            return result
        except ClientError as error:
            self.status.set(str(error))
        except OSError:
            self.status.set('文件不可访问；未启动真实整理。')
        return None

    def browse_workspace(self):
        value = filedialog.askdirectory(parent=self.root,title='选择独立的 D 盘空工作目录',mustexist=True)
        if value:
            self.work_var.set(value)

    def browse(self,field):
        if field == 'scansci_python':
            value = filedialog.askopenfilename(parent=self.root,title='选择 ScanSci Python 文件（不会运行）',
                                               filetypes=[('Python executable','*.exe')])
        else:
            value = filedialog.askdirectory(parent=self.root,title=dict(FIELDS)[field],mustexist=True)
        if value:
            self.vars[field].set(value)

    def load_config(self):
        result = self._action(lambda:Workspace(self.work_var.get()).config(),'已加载本工具配置；未操作文库或插件。')
        if result:
            for field,var in self.vars.items(): var.set(result[field])
        return result

    def save_config(self):
        def save():
            cfg = self._cfg()
            work = Workspace(self.work_var.get())
            work.initialize()
            work.save_config(cfg)
            return cfg
        return self._action(save,'配置已保存到独立工作目录；现有应用配置未改变。')

    def inspect(self):
        result = self._action(lambda:check_paths(self._cfg()),'只读准备检查完成；未检查运行状态、未连接网络。')
        if result:
            labels = dict(FIELDS, research_bridge='Research Bridge')
            lines = [f'{labels[key]}：{STATUS_TEXT.get(row["status"],row["status"])}'
                     + (f' / {row["version"]}' if row.get('version') else '') for key,row in result['checks'].items()]
            self._text(self.check_text,'\n'.join(lines)+'\n\n'+'\n'.join(result['blockers']))
        return result

    def simulate(self):
        def run():
            work = Workspace(self.work_var.get())
            work.initialize()
            return work.simulate()
        result = self._action(run,'模拟完成；真实下载/入库/笔记写入为 0。复跑不重试失败项。')
        if result: self._display(result)
        return result

    def refresh_report(self):
        result = self._action(lambda:Workspace(self.work_var.get()).report(),'已读取模拟账本；不代表真实服务队列。')
        if result: self._display(result)
        return result

    def _display(self,result):
        self.sim_result = result
        for child in self.tree.get_children(): self.tree.delete(child)
        for task in result['tasks']:
            values = (task['title'],task['category'],STATUS_TEXT[task['download_state']],
                      STATUS_TEXT[task['library_state']],STATUS_TEXT[task['note_state']])
            self.tree.insert('', 'end', iid=task['id'], values=values,tags=('blocked',) if task['error_code'] else ())
        counts = result['counts']
        self.summary.set(f'模拟任务 {counts["tasks"]} · 模拟已下载 {counts["simulated_downloaded"]} · '
                         f'未下载 {counts["undownloaded"]} · 待入库 {len(result["import_pending"])} · '
                         f'AI 暂停 {len(result["ai_paused"])} · 真实下载/入库 0')
        if result['tasks']:
            self.tree.selection_set(result['tasks'][0]['id'])
            self.show_task()

    def show_task(self,event=None):
        selected = self.tree.selection()
        if not selected: return
        task = next(t for t in self.sim_result['tasks'] if t['id'] == selected[0])
        text = f'{task["id"]} · {task["title"]}\n'
        text += (task['advice'] or '模拟流程完成；未生成真实 PDF 或文献卡。')+'\n\n'
        text += ' → '.join(task['events'])
        self._text(self.details,text)

    def export_workbuddy(self):
        def export():
            work = Workspace(self.work_var.get())
            work.initialize()
            return work.export_workbuddy(self.client_dir)
        result = self._action(export,'已导出模拟任务文件；未启动 WorkBuddy 或创建自动化。')
        if result:
            self._text(self.export_text,'任务文件：\n'+result['prompt']+'\n\nCLI 参数契约：\n'+result['contract']+
                       '\n\n请把任务文件交给 WorkBuddy；这里只导出，没有自动导入/启用。')
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path)
    args = parser.parse_args()
    root = tk.Tk()
    PaperFlowApp(root,args.workspace)
    root.mainloop()


if __name__ == '__main__':
    main()

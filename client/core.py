"""PaperFlow local phase. No network, live downloader, application writes or scheduler."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import sys
import tempfile

MAX_RECORD = 256 * 1024
MARKER = {"owner": "paperflow-local-client", "schema": 1}
CONFIG_KEYS = {"schema", "mode", "background_enabled", "service_url", "profile",
               "zotero_data", "obsidian_vault", "download_dir", "scansci_skill", "scansci_python"}


class ClientError(Exception):
    """Fixed safe messages only, never raw application content or credentials."""


def defaults():
    return {"schema": 1, "mode": "simulation", "background_enabled": False,
            "service_url": None, "profile": "optical_imaging", "zotero_data": "",
            "obsidian_vault": "", "download_dir": "D:/PaperFlow/downloads",
            "scansci_skill": "", "scansci_python": ""}


def validate_directory(value, write=False):
    if not isinstance(value, str) or not value or len(value) > 2048 or re.search(r'[\x00-\x1f<>"|?*]', value):
        raise ClientError("路径需为完整的本地目录，不含控制字符或通配符。")
    parsed = PureWindowsPath(value)
    if (not parsed.is_absolute() or not re.fullmatch(r'[A-Za-z]:', parsed.drive)
            or len(parsed.parts) < 2 or '..' in parsed.parts
            or (write and parsed.drive.upper() != 'D:')):
        raise ClientError("写入目录请选择 D 盘子目录；不允许盘符根目录、共享路径或相对路径。")
    if any(':' in part or part.rstrip(' .') != part or PureWindowsPath(part).is_reserved()
           for part in parsed.parts[1:]):
        raise ClientError("路径含不受支持的文件名。")
    return Path(value)


def validate_config(cfg):
    if not isinstance(cfg, dict) or set(cfg) != CONFIG_KEYS:
        raise ClientError("配置格式不支持；不接收令牌或 API 密钥字段。")
    if (type(cfg['schema']) is not int or cfg['schema'] != 1 or cfg['mode'] != 'simulation'
            or cfg['background_enabled'] is not False or cfg['service_url'] is not None
            or cfg['profile'] != 'optical_imaging'):
        raise ClientError("此版本仅支持本地模拟，真实服务、后台和付费 AI 未启用。")
    for field in ('zotero_data', 'obsidian_vault', 'download_dir', 'scansci_skill', 'scansci_python'):
        value = cfg[field]
        if not isinstance(value, str):
            raise ClientError("配置路径类型错误。")
        if value:
            validate_directory(value, write=field == 'download_dir')
    return copy.deepcopy(cfg)


def reparse(path):
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)
    except FileNotFoundError:
        return False


def read_record(path):
    if reparse(path) or path.stat().st_size > MAX_RECORD:
        raise ClientError("文件超限或指向外部位置，已停止读取。")
    with path.open('rb') as stream:
        raw = stream.read(MAX_RECORD + 1)
    if len(raw) > MAX_RECORD:
        raise ClientError("文件超出大小限制。")
    try:
        record = json.loads(raw.decode('utf-8-sig'))
    except (ValueError, UnicodeError, RecursionError):
        raise ClientError("记录损坏，已保留原文件；请勿直接覆盖。") from None
    if not isinstance(record, dict):
        raise ClientError("记录格式不支持。")
    return record


def check_paths(cfg):
    cfg = validate_config(cfg)
    checks = {}
    for field in ('zotero_data', 'obsidian_vault', 'download_dir', 'scansci_skill', 'scansci_python'):
        value = cfg[field]
        status = 'not_selected'
        if value:
            path = Path(value)
            try:
                expected_file = field == 'scansci_python'
                exists = path.is_file() if expected_file else path.is_dir()
                status = 'files_found_not_running_verified' if exists else 'missing'
                if exists and field == 'zotero_data' and not (path / 'zotero.sqlite').is_file():
                    status = 'zotero_marker_missing'
                if exists and field == 'obsidian_vault' and not (path / '.obsidian').is_dir():
                    status = 'obsidian_marker_missing'
                if exists and field == 'scansci_skill' and not (path / 'SKILL.md').is_file():
                    status = 'skill_marker_missing'
            except OSError:
                status = 'not_accessible'
        checks[field] = {'status':status}
    checks['research_bridge'] = {'status':'not_selected'}
    if cfg['obsidian_vault']:
        manifest = Path(cfg['obsidian_vault']) / '.obsidian/plugins/research-bridge/manifest.json'
        try:
            if manifest.exists():
                value = read_record(manifest)
                version = value.get('version')
                if value.get('id') != 'research-bridge' or not isinstance(version, str) or not re.fullmatch(r'[0-9A-Za-z.+-]{1,40}', version):
                    raise ClientError("插件标识不可确认。")
                checks['research_bridge'] = {'status':'files_found_not_enabled_verified', 'version':version}
            else:
                checks['research_bridge'] = {'status':'manifest_missing'}
        except (OSError, ClientError):
            checks['research_bridge'] = {'status':'manifest_unreadable'}
    return {'mode':'simulation', 'checks':checks, 'production_ready':False,
            'network_checked':False, 'programs_launched':False,
            'blockers':['正式服务与电脑绑定尚未接入。',
                        'scansci-pdf 实际可调用工具与合法来源策略尚未核验。',
                        '本阶段没有检查应用是否已运行/启用，也不会操作文库。']}


CASES = (
    ('SIM-OCT', '光学相干断层成像方法示例', 'OCT', 'ready'),
    ('SIM-FIBER', '光纤成像系统示例', '光纤成像', 'access_required'),
    ('SIM-COMPUTE', '计算成像重建方法示例', '计算成像', 'zotero_offline'),
    ('SIM-LIGHTSHEET', '光片显微成像方法示例', '光片显微', 'ai_paused'),
    ('SIM-REVIEW', '未明确成像方法的文章示例', '待复核', 'classification_review'),
)


def simulated_tasks():
    tasks = []
    for identifier, title, method, scenario in CASES:
        task = {'id':identifier, 'title':title, 'doi':None, 'simulation':True,
                'category':method, 'download_state':'simulation_downloaded',
                'library_state':'simulation_imported', 'note_state':'simulation_note_ready',
                'ai_state':'not_requested', 'download_attempts':1,
                'error_code':None, 'advice':None,
                'download_adapter':'fake_scansci_no_real_invocation',
                'skill_invoked':False, 'pdf_created':False,
                'events':['模拟接收文字消息', '模拟确认主论文', '模拟下载结果；未调用真实技能']}
        if scenario == 'access_required':
            task.update(download_state='simulation_not_downloaded', library_state='not_started',
                        note_state='not_started', error_code='needs_legal_access',
                        advice='模拟缺少合法全文访问；请提供合法来源或明确暂缓，不会自动弹出登录窗口。')
        elif scenario == 'zotero_offline':
            task.update(library_state='simulation_import_pending', note_state='not_started',
                        error_code='zotero_offline', advice='模拟 Zotero 未运行；等待用户处理，不重复下载。')
        elif scenario == 'ai_paused':
            task.update(ai_state='simulation_ai_paused', error_code='ai_preparation_failed',
                        advice='模拟 AI 准备暂停；已模拟入库/笔记完成，不自动重试或计费。')
        elif scenario == 'classification_review':
            task.update(error_code='imaging_method_uncertain',
                        advice='无法明确成像方法，保留待复核，不创建医学集合。')
        if task['library_state'] == 'simulation_imported':
            task['events'].extend(['模拟 DOI 去重/附件副本年份-题名命名', '模拟成像方法分类',
                                   '模拟 Research Bridge 文献卡；未写真实文库/笔记'])
        tasks.append(task)
    return tasks


def simulation_report(tasks):
    counts = {'tasks':len(tasks), 'real_downloads':0, 'real_imports':0,
              'simulated_downloaded':sum(t['download_state'] == 'simulation_downloaded' for t in tasks),
              'undownloaded':sum(t['download_state'] == 'simulation_not_downloaded' for t in tasks)}
    report = {'mode':'simulation', 'simulation_only':True, 'counts':counts,
              'tasks':copy.deepcopy(tasks), 'notification_sent':False,
              'undownloaded':[t for t in tasks if t['download_state'] == 'simulation_not_downloaded'],
              'import_pending':[t for t in tasks if t['library_state'] == 'simulation_import_pending'],
              'ai_paused':[t for t in tasks if t['ai_state'] == 'simulation_ai_paused'],
              'classification_review':[t for t in tasks if t['category'] == '待复核']}
    report['notification_fingerprint'] = hashlib.sha256(json.dumps(tasks, sort_keys=True,
                                                                ensure_ascii=False).encode()).hexdigest()
    return report


class Workspace:
    def __init__(self, root):
        self.root = validate_directory(str(root), write=True).absolute()

    def _safe_root(self):
        for path in (self.root, *self.root.parents):
            if reparse(path):
                raise ClientError("工作目录含链接/联接，已停止写入。")
            if (path / '.obsidian').exists() or (path / 'zotero.sqlite').exists():
                raise ClientError("工作目录不能放在已有 Obsidian 或 Zotero 数据目录内。")

    def _owned(self):
        self._safe_root()
        if not self.root.is_dir() or read_record(self.root / '.paperflow-client.json') != MARKER:
            raise ClientError("请选择并初始化独立的 PaperFlow 工作目录。")

    def initialize(self):
        self._safe_root()
        if self.root.exists():
            if not self.root.is_dir():
                raise ClientError("工作目录不是文件夹。")
            if (self.root / '.paperflow-client.json').exists():
                self._owned()
                return
            if next(self.root.iterdir(), None) is not None:
                raise ClientError("该目录已有其他文件，请选择新的空目录；不会覆盖。")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / '.paperflow-client.json'
        with path.open('x', encoding='utf-8') as stream:
            json.dump(MARKER, stream)

    def _read(self, name):
        self._owned()
        return read_record(self.root / name)

    def _write(self, name, content):
        self._owned()
        if name not in ('config.json', 'simulation.json', 'workbuddy-contract.json', 'workbuddy-task.md'):
            raise ClientError("不支持写入该文件。")
        target = self.root / name
        if reparse(target):
            raise ClientError("输出文件是链接，已停止。")
        if target.exists():
            if name == 'workbuddy-task.md':
                if target.stat().st_size > MAX_RECORD or not target.read_text(encoding='utf-8').startswith('<!-- paperflow-owned:simulation -->'):
                    raise ClientError("任务文件不属于本程序，不会覆盖。")
            elif name == 'config.json':
                validate_config(read_record(target))
            elif name == 'simulation.json':
                self._tasks()
            elif name == 'workbuddy-contract.json':
                old = read_record(target)
                if old.get('owner') != MARKER['owner'] or old.get('mode') != 'simulation':
                    raise ClientError("接入文件不属于本程序，不会覆盖。")
        raw = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.root, suffix='.tmp', delete=False) as stream:
            temp_path = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            self._safe_root()
            if reparse(target):
                raise ClientError("输出位置已变化，已停止。")
            os.replace(temp_path, target)
        finally:
            # Only the exact temporary file created by this call, inside the owned root.
            temp_path.unlink(missing_ok=True)

    def config(self):
        self._safe_root()
        if not self.root.exists():
            return defaults()
        self._owned()
        path = self.root / 'config.json'
        return validate_config(read_record(path)) if path.exists() else defaults()

    def save_config(self, cfg):
        cfg = validate_config(cfg)
        self.config()
        for field in ('zotero_data', 'obsidian_vault'):
            if cfg[field]:
                external = Path(cfg[field]).resolve()
                if self.root.resolve().is_relative_to(external) or external.is_relative_to(self.root.resolve()):
                    raise ClientError("工作目录和现有文库/笔记目录应相互独立。")
        self._write('config.json', cfg)

    def _tasks(self):
        payload = self._read('simulation.json')
        expected = {'schema':1, 'mode':'simulation', 'tasks':simulated_tasks()}
        if payload != expected:
            raise ClientError("模拟账本格式或内容异常，已保留文件并停止，不会改写成成功。")
        return copy.deepcopy(payload['tasks'])

    def report(self):
        self._safe_root()
        if not self.root.exists():
            return simulation_report([])
        self._owned()
        path = self.root / 'simulation.json'
        return simulation_report(self._tasks() if path.exists() else [])

    @contextmanager
    def _lock(self):
        self._owned()
        lock = self.root / 'simulation.lock'
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise ClientError("已有模拟运行锁；不会并发运行或删除其他进程的锁。") from None
        try:
            with os.fdopen(descriptor, 'w') as stream:
                stream.write(str(os.getpid()))
            yield
        finally:
            lock.unlink(missing_ok=True)

    def simulate(self):
        with self._lock():
            validate_config(self.config())
            if (self.root / 'simulation.json').exists():
                return self.report()
            self._write('simulation.json', {'schema':1,'mode':'simulation','tasks':simulated_tasks()})
            return self.report()

    def export_workbuddy(self, client_dir):
        client_dir = validate_directory(str(client_dir), write=True).resolve()
        cli = client_dir / 'cli.py'
        if not cli.is_file() or reparse(cli):
            raise ClientError("找不到受支持的本地 CLI 文件。")
        python = str(Path(sys.executable).resolve())
        contract = {'owner':MARKER['owner'], 'schema':1, 'mode':'simulation', 'production_enabled':False,
                    'commands':{operation:[python,'-B',str(cli),'--workspace',str(self.root),operation]
                                for operation in ('inspect','simulate','report')}}
        text = ('<!-- paperflow-owned:simulation -->\n# WorkBuddy 本地模拟任务\n\n'
                '使用 paperflow-workbuddy 技能的 simulation 模式。先读取同目录 workbuddy-contract.json，'
                '按 commands 中的参数数组执行 inspect、simulate、report，不把路径拼成 Shell 命令文本。'
                '解释逐篇未下载、待导入、AI 暂停、分类待复核；这些都是 SIM-* 模拟数据，真实 PDF/文库写入为 0。'
                '不要调用真实 scansci-pdf、下载论文、操作微信、写 Zotero/Obsidian、开启 AI 或建立自动化。'
                '任务仅写此已初始化的独立工作目录，结束立即退出。\n\n'
                '真实整理仍需正式服务、绑定、合法来源门禁与用户单独授权，不以模拟结果代替。\n')
        self._write('workbuddy-contract.json', contract)
        self._write('workbuddy-task.md', text)
        return {'contract':str(self.root / 'workbuddy-contract.json'),
                'prompt':str(self.root / 'workbuddy-task.md')}

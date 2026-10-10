(function () {
  'use strict';

  const { FilePicker } = ChromeUtils.importESModule(
    'chrome://zotero/content/modules/filePicker.mjs'
  );
  const PREFIX = 'extensions.zotero.wechatZoteroImporter.';

  function getPref(name) {
    return Zotero.Prefs.get(PREFIX + name, true);
  }

  function setPref(name, value) {
    Zotero.Prefs.set(PREFIX + name, value, true);
  }

  function element(id) {
    return document.getElementById(id);
  }

  function boundedInteger(id, fallback, minimum, maximum) {
    const value = Number(element(id).value);
    return Math.max(minimum, Math.min(maximum, Number.isFinite(value) ? Math.round(value) : fallback));
  }

  function status(message) {
    const target = element('wzi-status');
    if (target) target.textContent = message;
  }

  let profiles = [];

  function updateProfileDescription() {
    const selected = element('wzi-classification-profile').value;
    const profile = profiles.find((candidate) => candidate.id === selected);
    element('wzi-profile-description').textContent = profile
      ? `${profile.description}；Zotero 集合维度：${profile.collectionDimension}`
      : '分类配置不可用；保存前请重新打开设置页。';
  }

  async function onLoad() {
    element('wzi-enabled').checked = Boolean(getPref('enabled'));
    element('wzi-source-path').value = String(getPref('sourcePath') || '');
    element('wzi-sender-name').value = String(getPref('senderName') || '打更');
    element('wzi-import-history').checked = getPref('importHistory') !== false;
    element('wzi-scan-seconds').value = Number(getPref('scanIntervalSeconds')) || 30;
    element('wzi-quiet-seconds').value = Number(getPref('quietSeconds')) || 120;
    profiles = Zotero.WeChatZoteroImporter.getProfiles();
    const selector = element('wzi-classification-profile');
    selector.replaceChildren();
    for (const profile of profiles) {
      const option = document.createElement('option');
      option.value = profile.id;
      option.textContent = profile.label;
      selector.appendChild(option);
    }
    const savedProfile = String(getPref('classificationProfileId') || 'optical_imaging');
    selector.value = profiles.some((profile) => profile.id === savedProfile)
      ? savedProfile
      : 'optical_imaging';
    selector.addEventListener('change', updateProfileDescription);
    updateProfileDescription();
    const current = Zotero.WeChatZoteroImporter && Zotero.WeChatZoteroImporter.getStatus();
    status(current && current.running ? '正在后台运行' : '尚未运行');
  }

  async function browse() {
    const picker = new FilePicker();
    picker.init(window, '选择微信 PDF 文件夹', picker.modeGetFolder);
    const currentPath = element('wzi-source-path').value;
    if (currentPath) {
      try { picker.displayDirectory = currentPath; } catch (_error) {}
    }
    const result = await picker.show();
    if (result !== picker.returnOK) return;
    const selectedPath = (picker.file && typeof picker.file === 'object' && picker.file.path)
      ? picker.file.path
      : String(picker.file || '');
    const safetyError = Zotero.WeChatZoteroImporter.sourcePathSafetyError(selectedPath);
    if (safetyError) {
      Services.prompt.alert(window, '无法使用该文件夹', safetyError);
      return;
    }
    element('wzi-source-path').value = selectedPath;
    status('文件夹已选择，请保存');
  }

  async function save() {
    const sourcePath = element('wzi-source-path').value.trim();
    const enabled = element('wzi-enabled').checked;
    if (enabled) {
      const safetyError = Zotero.WeChatZoteroImporter.sourcePathSafetyError(sourcePath);
      if (safetyError) {
        Services.prompt.alert(window, '设置未保存', safetyError);
        return;
      }
      try {
        const info = await IOUtils.stat(sourcePath);
        if (info.type !== 'directory') throw new Error('not a directory');
      } catch (_error) {
        Services.prompt.alert(window, '设置未保存', '所选来源文件夹无法访问。');
        return;
      }
    }
    setPref('enabled', enabled);
    setPref('sourcePath', sourcePath);
    setPref('senderName', element('wzi-sender-name').value.trim() || '未设置发送方');
    setPref('importHistory', element('wzi-import-history').checked);
    setPref('scanIntervalSeconds', boundedInteger('wzi-scan-seconds', 30, 10, 3600));
    setPref('quietSeconds', boundedInteger('wzi-quiet-seconds', 120, 30, 3600));
    setPref('classificationProfileId', element('wzi-classification-profile').value);
    try {
      const running = await Zotero.WeChatZoteroImporter.restartWatcher();
      status(running ? '设置已保存，正在后台运行' : '设置已保存，当前已停用');
    } catch (error) {
      Zotero.logError(error);
      status(`启动失败：${error}`);
    }
  }

  async function scanNow() {
    status('正在扫描…');
    try {
      const scanned = await Zotero.WeChatZoteroImporter.scanNow();
      status(scanned ? '扫描完成；符合条件的文件已进入静默批次' : '未扫描，请先保存并启用');
    } catch (error) {
      Zotero.logError(error);
      status(`扫描失败：${error}`);
    }
  }

  async function reclassifyExisting() {
    const profileId = element('wzi-classification-profile').value;
    const profile = profiles.find((candidate) => candidate.id === profileId);
    if (!profile) {
      Services.prompt.alert(window, '无法重新分类', '当前分类模式无效。');
      return;
    }
    const count = await Zotero.WeChatZoteroImporter.countManagedItems();
    if (!count) {
      status('没有由本插件管理的已有条目');
      return;
    }
    const confirmed = Services.prompt.confirm(
      window,
      '重新分类已有条目',
      `将按“${profile.label}”重新分类 ${count} 个插件管理的条目。该操作会替换这些条目在“微信文献”下的旧分类，但保留其他集合。是否继续？`
    );
    if (!confirmed) return;
    status(`正在按“${profile.label}”重新分类 ${count} 个条目…`);
    try {
      const result = await Zotero.WeChatZoteroImporter.reclassifyExisting(profileId);
      status(`重新分类完成：成功 ${result.reclassified}，失败 ${result.failed}`);
    } catch (error) {
      Zotero.logError(error);
      status(`重新分类失败：${error}`);
    }
  }

  window.WeChatZoteroPrefs = { browse, onLoad, reclassifyExisting, save, scanNow };
})();

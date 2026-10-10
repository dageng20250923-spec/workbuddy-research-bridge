/* global Zotero, Services, IOUtils, PathUtils, ChromeUtils, fetch, crypto */

var WeChatZoteroBridge;
var WeChatZoteroWatcher;
var WeChatZoteroContext;

const WECHAT_ZOTERO_PREF_PREFIX = 'extensions.zotero.wechatZoteroImporter.';

function initDefaultPrefs() {
  const branch = Services.prefs.getDefaultBranch(WECHAT_ZOTERO_PREF_PREFIX);
  const defaults = {
    enabled: false,
    sourcePath: '',
    senderName: '打更',
    importHistory: true,
    scanIntervalSeconds: 30,
    quietSeconds: 120,
    classificationProfileId: 'optical_imaging',
    setupPrompted: false,
    migrationVersion: 0,
  };
  for (const [key, value] of Object.entries(defaults)) {
    try {
      if (typeof value === 'boolean') branch.setBoolPref(key, value);
      else if (typeof value === 'number') branch.setIntPref(key, value);
      else branch.setCharPref(key, value);
    } catch (_error) {}
  }
}

function getPluginPref(name) {
  return Zotero.Prefs.get(WECHAT_ZOTERO_PREF_PREFIX + name, true);
}

function setPluginPref(name, value) {
  Zotero.Prefs.set(WECHAT_ZOTERO_PREF_PREFIX + name, value, true);
}

function getStandalonePreferences() {
  return {
    enabled: Boolean(getPluginPref('enabled')),
    sourcePath: String(getPluginPref('sourcePath') || ''),
    senderName: String(getPluginPref('senderName') || '打更'),
    importHistory: getPluginPref('importHistory') !== false,
    scanIntervalSeconds: Math.max(10, Math.min(3600, Number(getPluginPref('scanIntervalSeconds')) || 30)),
    quietSeconds: Math.max(30, Math.min(3600, Number(getPluginPref('quietSeconds')) || 120)),
    classificationProfileId: String(getPluginPref('classificationProfileId') || 'optical_imaging'),
  };
}

function normalizedComparablePath(value) {
  return String(value || '').replace(/\\/g, '/').replace(/\/+$/, '').toLocaleLowerCase('en');
}

function sourcePathSafetyError(sourcePath) {
  const source = normalizedComparablePath(sourcePath);
  const data = normalizedComparablePath(Zotero.DataDirectory.dir);
  if (!source) return '尚未选择 PDF 来源文件夹。';
  if (source === data || source.startsWith(data + '/') || data.startsWith(source + '/')) {
    return '来源文件夹不能等于、包含或位于 Zotero 数据目录内。';
  }
  return '';
}

async function restartStandaloneWatcher() {
  if (!WeChatZoteroContext) return false;
  if (WeChatZoteroWatcher) await WeChatZoteroWatcher.stop();
  const prefs = getStandalonePreferences();
  if (!prefs.enabled || !prefs.sourcePath || sourcePathSafetyError(prefs.sourcePath)) {
    WeChatZoteroWatcher = null;
    return false;
  }
  const stat = await IOUtils.stat(prefs.sourcePath);
  if (stat.type !== 'directory') throw new Error('Configured PDF source path is not a directory');
  WeChatZoteroWatcher = WeChatZoteroContext.watcherModule.createRuntime({
    IOUtils,
    PathUtils,
    importService: WeChatZoteroBridge,
    statePath: WeChatZoteroContext.statePath,
    getPreferences: getStandalonePreferences,
    cryptoObject: crypto,
    logger(event, details) {
      Zotero.debug(`[WeChatZotero] ${event} ${JSON.stringify(details || {})}`);
    },
  });
  await WeChatZoteroWatcher.start();
  return true;
}

function install() {}
function uninstall() {}

async function startup({ rootURI, version }) {
  await Zotero.initializationPromise;
  initDefaultPrefs();

  const scope = {};
  scope._globalThis = scope;
  Services.scriptloader.loadSubScript(rootURI + 'classifier.js', scope);
  Services.scriptloader.loadSubScript(rootURI + 'standalone-watcher.js', scope);
  Services.scriptloader.loadSubScript(rootURI + 'migration.js', scope);
  const classificationConfig = await (
    await fetch(rootURI + 'classification-profiles.json')
  ).json();

  WeChatZoteroBridge = createBridge({
    classifier: scope.WeChatZoteroClassifier,
    IOUtils,
    PathUtils,
    classificationConfig,
    version,
  });
  await WeChatZoteroBridge.start();

  const statePath = PathUtils.join(
    Zotero.DataDirectory.dir,
    'wechat-zotero-importer',
    'standalone-state.json'
  );
  WeChatZoteroContext = {
    rootURI,
    classificationConfig,
    watcherModule: scope.WeChatZoteroStandaloneWatcher,
    statePath,
  };

  await Zotero.PreferencePanes.register({
    pluginID: 'wechat-zotero-importer@example.com',
    src: rootURI + 'preferences.xhtml',
    label: '微信 PDF 自动整理',
    scripts: [rootURI + 'preferences.js'],
  });

  Zotero.WeChatZoteroImporter = {
    restartWatcher: restartStandaloneWatcher,
    async scanNow() {
      if (!WeChatZoteroWatcher) await restartStandaloneWatcher();
      return WeChatZoteroWatcher ? WeChatZoteroWatcher.scanNow() : false;
    },
    getStatus() {
      return {
        running: Boolean(WeChatZoteroWatcher && WeChatZoteroWatcher.isRunning()),
        preferences: getStandalonePreferences(),
      };
    },
    getProfiles: () => WeChatZoteroBridge.getProfiles(),
    countManagedItems: () => WeChatZoteroBridge.countManagedItems(),
    reclassifyExisting: (profileId) => WeChatZoteroBridge.reclassifyExisting(profileId),
    sourcePathSafetyError,
  };

  try {
    const migration = scope.WeChatZoteroMigration.createMigration({
      Zotero,
      importService: WeChatZoteroBridge,
      rootName: classificationConfig.root || '微信文献',
      getMigrationVersion: () => getPluginPref('migrationVersion'),
      setMigrationVersion: (value) => setPluginPref('migrationVersion', value),
      logger(event, details) {
        Zotero.debug(`[WeChatZotero] ${event} ${JSON.stringify(details || {})}`);
      },
    });
    await migration.run();
  } catch (error) {
    Zotero.logError(`[WeChatZotero] Existing-item migration will retry next start: ${error}`);
  }

  try {
    await restartStandaloneWatcher();
  } catch (error) {
    Zotero.logError(`[WeChatZotero] Standalone watcher did not start: ${error}`);
  }
}

async function onMainWindowLoad({ window }) {
  if (getPluginPref('sourcePath') || getPluginPref('setupPrompted')) return;
  setPluginPref('setupPrompted', true);
  const shouldConfigure = Services.prompt.confirm(
    window,
    '微信 PDF 自动整理',
    '是否现在选择微信 PDF 所在文件夹？这是一次性设置；之后插件将在 Zotero 后台安静运行。'
  );
  if (!shouldConfigure) return;
  const { FilePicker } = ChromeUtils.importESModule('chrome://zotero/content/modules/filePicker.mjs');
  const picker = new FilePicker();
  picker.init(window, '选择微信 PDF 文件夹', picker.modeGetFolder);
  const result = await picker.show();
  if (result !== picker.returnOK) return;
  const selectedPath = (picker.file && typeof picker.file === 'object' && picker.file.path)
    ? picker.file.path
    : String(picker.file || '');
  const safetyError = sourcePathSafetyError(selectedPath);
  if (safetyError) {
    Services.prompt.alert(window, '无法使用该文件夹', safetyError);
    return;
  }
  setPluginPref('sourcePath', selectedPath);
  setPluginPref('enabled', true);
  setPluginPref('importHistory', true);
  await restartStandaloneWatcher();
}

function onMainWindowUnload() {}

async function shutdown() {
  if (WeChatZoteroWatcher) {
    await WeChatZoteroWatcher.stop();
    WeChatZoteroWatcher = null;
  }
  if (WeChatZoteroBridge) {
    WeChatZoteroBridge.stop();
    WeChatZoteroBridge = null;
  }
  delete Zotero.WeChatZoteroImporter;
  WeChatZoteroContext = null;
}

function createBridge({ classifier, IOUtils, PathUtils, classificationConfig, version }) {
  const PING_PATH = '/wechat-zotero/ping';
  const IMPORT_PATH = '/wechat-zotero/import';
  let token = '';

  function jsonResponse(status, body) {
    return [status, 'application/json; charset=utf-8', JSON.stringify(body)];
  }

  function requestToken(headers) {
    return headers['x-wechat-zotero-token'] || headers['X-WeChat-Zotero-Token'] || '';
  }

  async function ensureToken() {
    const base = Services.env.get('LOCALAPPDATA');
    if (!base) throw new Error('LOCALAPPDATA is unavailable');
    const directory = PathUtils.join(base, 'WeChatZoteroImporter');
    const tokenPath = PathUtils.join(directory, 'bridge-token');
    await IOUtils.makeDirectory(directory, { ignoreExisting: true });
    if (await IOUtils.exists(tokenPath)) {
      token = (await IOUtils.readUTF8(tokenPath)).trim();
    }
    if (!/^[A-Za-z0-9]{32,}$/.test(token)) {
      token = Zotero.Utilities.randomString(48);
      await IOUtils.writeUTF8(tokenPath, token + '\n');
    }
  }

  async function findByHash(libraryID, digest) {
    const search = new Zotero.Search();
    search.libraryID = libraryID;
    search.addCondition('tag', 'is', `wechat-zotero-sha256:${digest}`);
    const ids = await search.search();
    return ids.length ? await Zotero.Items.getAsync(ids[0]) : null;
  }

  async function getOrCreateCollection(libraryID, segments) {
    let parentID = false;
    let current = null;
    for (const name of segments) {
      const siblings = parentID
        ? Zotero.Collections.getByParent(parentID)
        : Zotero.Collections.getByLibrary(libraryID);
      current = siblings.find((collection) => collection.name === name);
      if (!current) {
        current = new Zotero.Collection();
        current.libraryID = libraryID;
        current.name = name;
        if (parentID) current.parentID = parentID;
        await current.saveTx();
      }
      parentID = current.id;
    }
    return current;
  }

  async function verifyPDF(filePath) {
    if (!(await IOUtils.exists(filePath))) throw new Error('PDF file does not exist');
    const stat = await IOUtils.stat(filePath);
    if (stat.type !== 'regular') throw new Error('PDF path is not a regular file');
    const header = await IOUtils.read(filePath, { maxBytes: 5 });
    const magic = String.fromCharCode(...header);
    if (magic !== '%PDF-') throw new Error('File does not have a PDF header');
  }

  async function findStoredPDFAttachment(item) {
    if (item.isAttachment && item.isAttachment()) {
      return !item.isStoredFileAttachment || item.isStoredFileAttachment() ? item : null;
    }
    if (!item.isRegularItem || !item.isRegularItem()) return null;
    const attachmentIDs = item.getAttachments();
    const attachments = attachmentIDs.length ? await Zotero.Items.getAsync(attachmentIDs) : [];
    return attachments.find((candidate) => {
      if (!candidate || !candidate.isAttachment || !candidate.isAttachment()) return false;
      if (candidate.isStoredFileAttachment && !candidate.isStoredFileAttachment()) return false;
      const filename = candidate.attachmentFilename || '';
      return candidate.attachmentContentType === 'application/pdf' || /\.pdf$/i.test(filename);
    }) || null;
  }

  async function renameStoredAttachment(item, fallbackPath) {
    try {
      const attachment = await findStoredPDFAttachment(item);
      if (!attachment) {
        return { renamed: false, attachmentFilename: null, renameError: 'No stored PDF attachment found' };
      }
      const metadataItem = attachment.parentItemID
        ? await Zotero.Items.getAsync(attachment.parentItemID)
        : item;
      const isRegular = metadataItem && metadataItem.isRegularItem && metadataItem.isRegularItem();
      const title = isRegular ? metadataItem.getField('title') : '';
      const date = isRegular ? metadataItem.getField('date') : '';
      const originalFilename = attachment.attachmentFilename || PathUtils.filename(fallbackPath);
      const requestedName = classifier.buildAttachmentFilename({
        year: date,
        title,
        originalFilename,
        maxLength: 180,
      });
      const validName = Zotero.File.truncateFileName(
        Zotero.File.getValidFileName(requestedName),
        180
      );
      if (attachment.attachmentFilename === validName) {
        return { renamed: false, attachmentFilename: validName, renameError: null };
      }
      const renameResult = await attachment.renameAttachmentFile(validName, { unique: true, updateTitle: true });
      if (renameResult !== true) {
        return {
          renamed: false,
          attachmentFilename: attachment.attachmentFilename || originalFilename,
          renameError: `Zotero rename result: ${renameResult}`,
        };
      }
      return {
        renamed: true,
        attachmentFilename: attachment.attachmentFilename || validName,
        renameError: null,
      };
    } catch (error) {
      Zotero.logError(error);
      return { renamed: false, attachmentFilename: null, renameError: String(error) };
    }
  }

  function isOperationalTag(tagName) {
    return /^(?:wechat-zotero-sha256:|来源\/微信\/|触发方式\/)/.test(tagName);
  }

  async function itemCollectionIDs(item) {
    return Zotero.DB.columnQueryAsync(
      'SELECT collectionID FROM collectionItems WHERE itemID = ?',
      [item.id]
    );
  }

  function collectionDescendantIDs(root) {
    return root.getDescendents(false, null, true)
      .filter((row) => row.type === 'collection')
      .map((row) => row.id);
  }

  async function classifyAndPlace(target, attachment, data, digest) {
    const attachmentText = attachment ? await attachment.attachmentText : '';
    const title = target.isRegularItem() ? target.getField('title') : target.getDisplayTitle();
    const abstract = target.isRegularItem() ? target.getField('abstractNote') : '';
    const profileId = String(data.classificationProfileId || 'optical_imaging');
    let result;
    let paths;
    try {
      result = classifier.classifyProfile(classificationConfig, profileId, {
        title,
        abstract,
        text: (attachmentText || '').slice(0, 120000),
        hasExtractableText: Boolean(attachmentText && attachmentText.trim()),
      });
      paths = classifier.collectionPaths(classificationConfig, result);
    } catch (error) {
      result = {
        profileId,
        profileLabel: profileId,
        primaryCategory: null,
        keywords: [],
        confidence: 0,
        pending: true,
        configError: String(error),
      };
      paths = [[classificationConfig.root || '微信文献']];
    }

    for (const tag of target.getTags()) {
      if (Number(tag.type) === 1 && !isOperationalTag(tag.tag)) {
        target.removeTag(tag.tag);
      }
    }
    const tagNames = [
      ...result.keywords,
      `分类模式/${result.profileLabel}`,
      ...(result.configError ? ['分类状态/配置错误'] : []),
      ...(!result.configError && result.pending ? ['分类状态/待识别'] : []),
      ...(!result.configError && !result.pending
        ? [`分类置信度/${result.confidence >= 0.75 ? '高' : '中'}`]
        : []),
      ...(data.sourceName ? [classifier.buildSourceTag(data.sourceName)] : []),
      ...classifier.normalizeExtraTags(data.extraTags),
      `wechat-zotero-sha256:${digest}`,
    ];
    for (const tag of classifier.unique(tagNames)) target.addTag(tag, 1);

    const collections = [];
    for (const segments of paths) {
      collections.push(await getOrCreateCollection(target.libraryID, segments));
    }
    const rootName = classificationConfig.root || '微信文献';
    const pluginRoots = Zotero.Collections.getByLibrary(target.libraryID)
      .filter((collection) => collection.name === rootName);
    const pluginCollectionIDs = new Set(pluginRoots.flatMap((root) => (
      [root.id, ...collectionDescendantIDs(root)]
    )));
    const externalCollectionIDs = (await itemCollectionIDs(target))
      .filter((id) => !pluginCollectionIDs.has(id));
    target.setCollections(classifier.unique([
      ...externalCollectionIDs,
      ...collections.map((collection) => collection.id),
    ]));
    await target.saveTx();
    return { result, paths };
  }

  async function importPDF(data) {
    const validationErrors = classifier.validateImportRequest(data);
    if (validationErrors.length) {
      const error = new Error(validationErrors.join('; '));
      error.status = 400;
      throw error;
    }

    const filePath = data.path;
    const digest = data.sha256.toLocaleLowerCase('en');
    await verifyPDF(filePath);
    const libraryID = Zotero.Libraries.userLibraryID;
    const duplicate = await findByHash(libraryID, digest);
    if (duplicate) {
      const duplicateTagNames = classifier.unique([
        classifier.buildSourceTag(data.sourceName),
        ...classifier.normalizeExtraTags(data.extraTags),
      ]);
      for (const tag of duplicateTagNames) duplicate.addTag(tag, 1);
      await duplicate.saveTx();
      const rename = await renameStoredAttachment(duplicate, filePath);
      return {
        status: 'duplicate',
        itemKey: duplicate.key,
        title: duplicate.getDisplayTitle(),
        classificationPreserved: true,
        ...rename,
      };
    }

    const attachment = await Zotero.Attachments.importFromFile({
      file: filePath,
      libraryID,
      title: PathUtils.filename(filePath),
      contentType: 'application/pdf',
    });

    let recognitionError = null;
    try {
      await Zotero.RecognizeDocument.recognizeItems([attachment]);
    } catch (error) {
      recognitionError = String(error);
      Zotero.logError(error);
    }

    try {
      await Zotero.Fulltext.indexItems([attachment.id], { complete: false, ignoreErrors: true });
    } catch (error) {
      Zotero.logError(error);
    }

    const target = attachment.parentItemID
      ? await Zotero.Items.getAsync(attachment.parentItemID)
      : attachment;
    const classification = await classifyAndPlace(target, attachment, data, digest);
    const rename = await renameStoredAttachment(target, filePath);

    return {
      status: 'imported',
      itemKey: target.key,
      attachmentKey: attachment.key,
      title: target.getDisplayTitle(),
      recognized: Boolean(attachment.parentItemID),
      recognitionError,
      keywords: classification.result.keywords,
      categories: classification.paths,
      confidence: classification.result.confidence,
      ...rename,
    };
  }

  function PingEndpoint() {}
  PingEndpoint.prototype = {
    supportedMethods: ['GET'],
    init(_request) {
      return jsonResponse(200, { ok: true, version, zoteroVersion: Zotero.version });
    },
  };

  function ImportEndpoint() {}
  ImportEndpoint.prototype = {
    supportedMethods: ['POST'],
    supportedDataTypes: ['application/json'],
    async init(request) {
      if (!token || requestToken(request.headers) !== token) {
        return jsonResponse(401, { error: 'unauthorized' });
      }
      try {
        return jsonResponse(200, await importPDF(request.data));
      } catch (error) {
        Zotero.logError(error);
        return jsonResponse(error.status || 500, { error: String(error.message || error) });
      }
    },
  };

  async function managedItemIDs() {
    return Zotero.DB.columnQueryAsync(
      "SELECT DISTINCT itemTags.itemID FROM itemTags JOIN tags USING (tagID) "
        + "JOIN items USING (itemID) WHERE tags.name LIKE 'wechat-zotero-sha256:%' "
        + 'AND items.libraryID = ?',
      [Zotero.Libraries.userLibraryID]
    );
  }

  function digestFromItem(item) {
    const prefix = 'wechat-zotero-sha256:';
    const tag = item.getTags().find((entry) => String(entry.tag || '').startsWith(prefix));
    return tag ? String(tag.tag).slice(prefix.length).toLocaleLowerCase('en') : '';
  }

  async function reclassifyExisting(profileId) {
    classifier.getProfile(classificationConfig, profileId);
    const ids = await managedItemIDs();
    let reclassified = 0;
    let failed = 0;
    for (const id of ids) {
      try {
        const item = await Zotero.Items.getAsync(id);
        if (!item || item.deleted) continue;
        const digest = digestFromItem(item);
        const attachment = await findStoredPDFAttachment(item);
        if (!digest || !attachment) throw new Error('Managed PDF attachment is unavailable');
        await classifyAndPlace(item, attachment, {
          classificationProfileId: profileId,
          sourceName: '',
          extraTags: [],
        }, digest);
        reclassified += 1;
      } catch (error) {
        failed += 1;
        Zotero.logError(error);
      }
    }
    return { total: ids.length, reclassified, failed };
  }

  return {
    importPDF,
    classifyAndPlace,
    countManagedItems: async () => (await managedItemIDs()).length,
    findStoredPDFAttachment,
    getProfiles: () => classifier.listProfiles(classificationConfig),
    getOrCreateCollection,
    reclassifyExisting,
    async start() {
      await ensureToken();
      Zotero.Server.Endpoints[PING_PATH] = PingEndpoint;
      Zotero.Server.Endpoints[IMPORT_PATH] = ImportEndpoint;
      Zotero.debug('WeChat Zotero Importer endpoint registered');
    },
    stop() {
      delete Zotero.Server.Endpoints[PING_PATH];
      delete Zotero.Server.Endpoints[IMPORT_PATH];
    },
  };
}

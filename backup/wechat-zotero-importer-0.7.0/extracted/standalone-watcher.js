(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  } else {
    root.WeChatZoteroStandaloneWatcher = api;
  }
})(typeof _globalThis !== 'undefined' ? _globalThis : (typeof globalThis !== 'undefined' ? globalThis : this), function () {
  'use strict';

  const RETRY_DELAYS_MS = [60_000, 120_000, 300_000, 900_000];

  function createState() {
    return {
      version: 1,
      sourceRoot: '',
      initialized: false,
      files: {},
      pending: {},
      quietUntil: null,
      batch: null,
      completedHashes: {},
    };
  }

  function signature(file) {
    return { size: Number(file.size || 0), mtime: Number(file.mtime || 0) };
  }

  function sameSignature(left, right) {
    return Boolean(left && right)
      && Number(left.size) === Number(right.size)
      && Number(left.mtime) === Number(right.mtime);
  }

  function normalizeScanFiles(files) {
    return (Array.isArray(files) ? files : [])
      .filter((file) => file && file.type !== 'directory' && /\.pdf$/i.test(String(file.path || '')))
      .map((file) => ({
        path: String(file.path),
        size: Number(file.size || 0),
        mtime: Number(file.mtime || 0),
      }))
      .sort((a, b) => a.path.toLocaleLowerCase('en').localeCompare(b.path.toLocaleLowerCase('en')));
  }

  function selectSourceRoot(state, sourcePath) {
    const nextRoot = String(sourcePath || '');
    if (state.sourceRoot === nextRoot) return false;
    const reset = createState();
    reset.sourceRoot = nextRoot;
    for (const key of Object.keys(state)) delete state[key];
    Object.assign(state, reset);
    return true;
  }

  function reconcileState(state, files, nowMs, options = {}) {
    const quietMs = Math.max(0, Number(options.quietMs ?? 120_000));
    const normalized = normalizeScanFiles(files);
    let changed = 0;

    if (!state.initialized) {
      state.initialized = true;
      for (const file of normalized) {
        state.files[file.path] = signature(file);
        if (options.importHistory !== false) {
          state.pending[file.path] = signature(file);
          changed += 1;
        }
      }
      if (changed) state.quietUntil = Number(nowMs) + quietMs;
      return { changed, initialized: true };
    }

    for (const file of normalized) {
      const next = signature(file);
      if (sameSignature(state.files[file.path], next)) continue;
      state.files[file.path] = next;
      state.pending[file.path] = next;
      changed += 1;
    }
    if (changed) state.quietUntil = Number(nowMs) + quietMs;
    return { changed, initialized: false };
  }

  function freezeDueBatch(state, nowMs, options = {}) {
    if (state.batch) return state.batch;
    const paths = Object.keys(state.pending).sort((a, b) => (
      a.toLocaleLowerCase('en').localeCompare(b.toLocaleLowerCase('en'))
    ));
    if (!paths.length || state.quietUntil === null || Number(nowMs) < Number(state.quietUntil)) {
      return null;
    }
    const items = {};
    for (const path of paths) {
      items[path] = {
        ...state.pending[path],
        attempts: 0,
        nextAttempt: 0,
        lastError: null,
      };
    }
    state.pending = {};
    state.quietUntil = null;
    state.batch = {
      id: `batch-${Number(nowMs)}`,
      createdAt: Number(nowMs),
      classificationProfileId: String(options.classificationProfileId || 'optical_imaging'),
      paths,
      items,
    };
    return state.batch;
  }

  function retryDelayMs(attempts) {
    const index = Math.max(0, Math.min(RETRY_DELAYS_MS.length - 1, Number(attempts || 1) - 1));
    return RETRY_DELAYS_MS[index];
  }

  function readyBatchItem(state, nowMs) {
    if (!state.batch) return null;
    return state.batch.paths.find((path) => (
      state.batch.items[path] && Number(state.batch.items[path].nextAttempt || 0) <= Number(nowMs)
    )) || null;
  }

  function completeBatchItem(state, path, outcome, nowMs) {
    if (!state.batch || !state.batch.items[path]) return false;
    const item = state.batch.items[path];
    if (!outcome || outcome.ok !== true) {
      item.attempts = Number(item.attempts || 0) + 1;
      item.lastError = String((outcome && outcome.error) || 'unknown import error');
      item.nextAttempt = Number(nowMs) + retryDelayMs(item.attempts);
      return false;
    }
    if (outcome.digest) state.completedHashes[String(outcome.digest).toLowerCase()] = Number(nowMs);
    delete state.batch.items[path];
    state.batch.paths = state.batch.paths.filter((candidate) => candidate !== path);
    if (!state.batch.paths.length) state.batch = null;
    return true;
  }

  async function scanFolderRecursive(IOUtils, sourcePath, maxDepth = 6) {
    const output = [];
    const visit = async (directory, depth) => {
      if (depth > maxDepth) return;
      const children = await IOUtils.getChildren(directory);
      for (const child of children) {
        let info;
        try {
          info = await IOUtils.stat(child);
        } catch (_error) {
          continue;
        }
        if (info.type === 'directory') {
          await visit(child, depth + 1);
        } else if (info.type === 'regular' && /\.pdf$/i.test(child)) {
          output.push({
            path: child,
            type: info.type,
            size: Number(info.size || 0),
            mtime: Number(info.lastModified || 0),
          });
        }
      }
    };
    await visit(sourcePath, 0);
    return normalizeScanFiles(output);
  }

  async function sha256File(IOUtils, filePath, cryptoObject) {
    const bytes = await IOUtils.read(filePath);
    const digest = await cryptoObject.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest))
      .map((value) => value.toString(16).padStart(2, '0'))
      .join('');
  }

  function createRuntime(options) {
    const {
      IOUtils,
      PathUtils,
      importService,
      statePath,
      getPreferences,
      cryptoObject,
      logger = () => {},
    } = options;
    let state = createState();
    let running = false;
    let timer = null;
    let scanRunning = false;

    async function loadState() {
      try {
        if (await IOUtils.exists(statePath)) {
          const loaded = await IOUtils.readJSON(statePath);
          if (loaded && loaded.version === 1) state = { ...createState(), ...loaded };
        }
      } catch (error) {
        logger('state_load_error', { error: String(error) });
      }
    }

    async function saveState() {
      const directory = PathUtils.parent(statePath);
      await IOUtils.makeDirectory(directory, { createAncestors: true, ignoreExisting: true });
      await IOUtils.writeJSON(statePath, state, { tmpPath: `${statePath}.tmp` });
    }

    function schedule(delayMs) {
      if (!running) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(async () => {
        timer = null;
        try {
          await scanOnce();
        } catch (error) {
          logger('scan_error', { error: String(error) });
        } finally {
          const prefs = getPreferences();
          schedule(Math.max(5_000, Number(prefs.scanIntervalSeconds || 30) * 1_000));
        }
      }, Math.max(0, Number(delayMs || 0)));
    }

    async function processBatch() {
      while (running && state.batch) {
        const path = readyBatchItem(state, Date.now());
        if (!path) break;
        try {
          const prefs = getPreferences();
          if (!(await IOUtils.exists(path))) {
            delete state.files[path];
            completeBatchItem(state, path, { ok: true }, Date.now());
            logger('source_file_missing', { path, action: 'skipped' });
            await saveState();
            continue;
          }
          const current = await IOUtils.stat(path);
          const expected = state.batch.items[path];
          const currentSignature = {
            size: Number(current.size || 0),
            mtime: Number(current.lastModified || 0),
          };
          if (!sameSignature(expected, currentSignature)) {
            state.pending[path] = currentSignature;
            state.files[path] = currentSignature;
            state.quietUntil = Date.now() + Number(prefs.quietSeconds || 120) * 1_000;
            completeBatchItem(state, path, { ok: true }, Date.now());
            await saveState();
            continue;
          }
          const digest = await sha256File(IOUtils, path, cryptoObject);
          const result = await importService.importPDF({
            path,
            sha256: digest,
            sourceName: prefs.senderName || '未设置发送方',
            extraTags: ['触发方式/目录静默批次'],
            classificationProfileId: String(
              state.batch.classificationProfileId || 'optical_imaging'
            ),
          });
          completeBatchItem(state, path, { ok: true, digest }, Date.now());
          logger('import_result', { path, ...result });
        } catch (error) {
          completeBatchItem(state, path, { ok: false, error: String(error) }, Date.now());
          logger('import_error', { path, error: String(error) });
        }
        await saveState();
      }
    }

    async function scanOnce() {
      if (!running || scanRunning) return false;
      scanRunning = true;
      try {
        const prefs = getPreferences();
        if (!prefs.enabled || !prefs.sourcePath) return false;
        selectSourceRoot(state, prefs.sourcePath);
        const files = await scanFolderRecursive(IOUtils, prefs.sourcePath);
        const result = reconcileState(state, files, Date.now(), {
          importHistory: prefs.importHistory !== false,
          quietMs: Number(prefs.quietSeconds || 120) * 1_000,
        });
        if (result.changed) logger('files_staged', { count: result.changed });
        freezeDueBatch(state, Date.now(), {
          classificationProfileId: prefs.classificationProfileId || 'optical_imaging',
        });
        await saveState();
        await processBatch();
        return true;
      } finally {
        scanRunning = false;
      }
    }

    return {
      async start() {
        if (running) return;
        await loadState();
        running = true;
        await scanOnce();
        const prefs = getPreferences();
        schedule(Math.max(5_000, Number(prefs.scanIntervalSeconds || 30) * 1_000));
      },
      async stop() {
        running = false;
        if (timer) clearTimeout(timer);
        timer = null;
        await saveState();
      },
      async scanNow() {
        return scanOnce();
      },
      getState() {
        return state;
      },
      isRunning() {
        return running;
      },
    };
  }

  return {
    completeBatchItem,
    createRuntime,
    createState,
    freezeDueBatch,
    normalizeScanFiles,
    readyBatchItem,
    reconcileState,
    retryDelayMs,
    scanFolderRecursive,
    selectSourceRoot,
    sha256File,
  };
});

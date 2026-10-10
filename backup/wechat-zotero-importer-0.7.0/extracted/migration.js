(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  } else {
    root.WeChatZoteroMigration = api;
  }
})(typeof _globalThis !== 'undefined' ? _globalThis : (typeof globalThis !== 'undefined' ? globalThis : this), function () {
  'use strict';

  const TARGET_VERSION = 1;
  const HASH_PREFIX = 'wechat-zotero-sha256:';
  const LEGACY_BRANCHES = new Set(['医学', '光学', '医学光学交叉', '管理']);

  function digestFromTags(tags) {
    for (const entry of Array.isArray(tags) ? tags : []) {
      const name = String(entry && entry.tag || '');
      if (name.startsWith(HASH_PREFIX) && /^[a-f0-9]{64}$/i.test(name.slice(HASH_PREFIX.length))) {
        return name.slice(HASH_PREFIX.length).toLowerCase();
      }
    }
    return null;
  }

  function isLegacyBranchName(name) {
    return LEGACY_BRANCHES.has(String(name || ''));
  }

  function createMigration(options) {
    const {
      Zotero,
      importService,
      rootName = '微信文献',
      getMigrationVersion,
      setMigrationVersion,
      logger = () => {},
    } = options;

    async function managedItemIDs() {
      return Zotero.DB.columnQueryAsync(
        "SELECT DISTINCT itemTags.itemID FROM itemTags JOIN tags USING (tagID) "
          + "JOIN items USING (itemID) WHERE tags.name LIKE 'wechat-zotero-sha256:%' "
          + 'AND items.libraryID = ?',
        [Zotero.Libraries.userLibraryID]
      );
    }

    async function migrateItem(item) {
      if (!item || item.deleted) return false;
      const digest = digestFromTags(item.getTags());
      if (!digest) return false;
      const attachment = await importService.findStoredPDFAttachment(item);
      if (!attachment) {
        logger('migration_attachment_missing', { itemID: item.id, itemKey: item.key });
        return false;
      }
      await importService.classifyAndPlace(item, attachment, {
        classificationProfileId: 'optical_imaging',
        sourceName: '',
        extraTags: [],
      }, digest);
      return true;
    }

    async function cleanupLegacyBranches() {
      const roots = Zotero.Collections.getByLibrary(Zotero.Libraries.userLibraryID)
        .filter((collection) => collection.name === rootName);
      let removed = 0;
      for (const root of roots) {
        const children = Zotero.Collections.getByParent(root.id)
          .filter((collection) => isLegacyBranchName(collection.name));
        for (const child of children) {
          const descendants = child.getDescendents(false, null, true);
          if (descendants.some((row) => row.type === 'item')) continue;
          await child.eraseTx();
          removed += 1;
        }
      }
      return removed;
    }

    async function run() {
      if (Number(getMigrationVersion() || 0) >= TARGET_VERSION) {
        return { status: 'current', migrated: 0, failed: 0, removedBranches: 0 };
      }
      let migrated = 0;
      let failed = 0;
      const ids = await managedItemIDs();
      for (const id of ids) {
        try {
          const item = await Zotero.Items.getAsync(id);
          if (await migrateItem(item)) migrated += 1;
        } catch (error) {
          failed += 1;
          logger('migration_item_error', { itemID: id, error: String(error) });
        }
      }
      const removedBranches = await cleanupLegacyBranches();
      if (failed) {
        throw new Error(`Existing-item migration failed for ${failed} item(s)`);
      }
      setMigrationVersion(TARGET_VERSION);
      logger('migration_complete', { migrated, removedBranches });
      return { status: 'migrated', migrated, failed, removedBranches };
    }

    return { run };
  }

  return {
    TARGET_VERSION,
    createMigration,
    digestFromTags,
    isLegacyBranchName,
  };
});

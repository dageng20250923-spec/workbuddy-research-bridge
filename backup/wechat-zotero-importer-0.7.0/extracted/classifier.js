(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  } else {
    root.WeChatZoteroClassifier = api;
  }
})(typeof _globalThis !== 'undefined' ? _globalThis : (typeof globalThis !== 'undefined' ? globalThis : this), function () {
  'use strict';

  function normalizeText(value) {
    return String(value || '')
      .toLocaleLowerCase('en')
      .replace(/[‐–—]/g, '-')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function unique(values) {
    const seen = new Set();
    return values.filter((value) => {
      const key = String(value).toLocaleLowerCase('en');
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  function containsKeyword(text, keyword) {
    const normalized = normalizeText(keyword);
    if (!normalized) return false;
    if (/^[\x00-\x7F]+$/.test(normalized)) {
      return new RegExp(`(^|[^a-z0-9])${escapeRegExp(normalized)}([^a-z0-9]|$)`).test(text);
    }
    return text.includes(normalized);
  }

  function countKeyword(text, keyword) {
    const normalized = normalizeText(keyword);
    if (!normalized || !text) return 0;
    const boundary = /^[\x00-\x7F]+$/.test(normalized);
    const expression = boundary
      ? new RegExp(`(^|[^a-z0-9])${escapeRegExp(normalized)}(?=[^a-z0-9]|$)`, 'g')
      : new RegExp(escapeRegExp(normalized), 'g');
    return (text.match(expression) || []).length;
  }

  function listProfiles(config) {
    return (config && Array.isArray(config.profiles) ? config.profiles : []).map((profile) => ({
      id: String(profile.id),
      label: String(profile.label),
      description: String(profile.description || ''),
      collectionDimension: String(profile.collection_dimension || '分类'),
    }));
  }

  function getProfile(config, profileId) {
    const profile = (config && Array.isArray(config.profiles) ? config.profiles : [])
      .find((candidate) => String(candidate.id) === String(profileId));
    if (!profile) throw new Error(`Unknown classification profile: ${profileId}`);
    if (!Array.isArray(profile.categories) || !profile.categories.length) {
      throw new Error(`Invalid classification profile: ${profileId}`);
    }
    return profile;
  }

  function classifyProfile(config, profileId, input = {}) {
    const profile = getProfile(config, profileId);
    const fields = [
      { text: normalizeText(input.title), weight: 3, repeatRequired: false },
      { text: normalizeText(input.abstract), weight: 2, repeatRequired: false },
      { text: normalizeText(input.text), weight: 1, repeatRequired: true },
    ];
    const rows = profile.categories.map((category, index) => {
      const fieldScores = fields.map((field) => {
        const occurrences = (category.aliases || []).reduce(
          (total, alias) => total + countKeyword(field.text, alias), 0
        );
        if (field.repeatRequired && occurrences < 2) return 0;
        return occurrences ? field.weight * occurrences : 0;
      });
      return {
        index,
        label: String(category.label),
        fieldScores,
        score: fieldScores.reduce((total, value) => total + value, 0),
      };
    });
    const evidenceIndex = fields.findIndex((_field, index) => rows.some((row) => row.fieldScores[index]));
    const ranked = evidenceIndex < 0 ? [] : rows
      .filter((row) => row.fieldScores[evidenceIndex] > 0)
      .sort((left, right) => right.score - left.score || left.index - right.index);
    const minimum = Number(profile.minimum_score || 2);
    const margin = Number(profile.minimum_margin || 1);
    const top = ranked[0];
    const runnerUp = ranked[1];
    const accepted = Boolean(top)
      && top.score >= minimum
      && (!runnerUp || top.score - runnerUp.score >= margin);
    const pending = !accepted;
    const keywords = pending
      ? ['待识别']
      : unique(ranked.filter((row) => row.score >= minimum).map((row) => row.label))
        .slice(0, Math.max(1, Math.min(3, Number(profile.max_keywords || 3))));
    return {
      profileId: String(profile.id),
      profileLabel: String(profile.label),
      collectionDimension: String(profile.collection_dimension || '分类'),
      primaryCategory: pending ? '待识别' : top.label,
      categories: [],
      keywords,
      confidence: pending ? 0 : Math.min(1, top.score / 6),
      pending,
      managementPath: null,
    };
  }

  function classify(taxonomy, input) {
    const fields = [
      [normalizeText(input.title), 3],
      [normalizeText(input.abstract), 2],
      [normalizeText(input.text), 1],
    ];
    const methodScores = [];
    for (const [index, method] of (taxonomy.imaging_methods || []).entries()) {
      const fieldScores = [];
      for (const [field, weight] of fields) {
        let fieldScore = 0;
        for (const alias of (method.aliases || [])) {
          if (containsKeyword(field, alias)) fieldScore += weight;
        }
        fieldScores.push(fieldScore);
      }
      const score = fieldScores.reduce((total, value) => total + value, 0);
      if (score) {
        methodScores.push({ index, score, fieldScores, label: String(method.label) });
      }
    }
    const evidenceIndex = fields.findIndex((_field, index) => (
      methodScores.some((row) => row.fieldScores[index] > 0)
    ));
    const minimum = Number(taxonomy.imaging_method_min_score || 2);
    const eligibleMethods = evidenceIndex < 0 ? [] : methodScores.filter((row) => (
      row.fieldScores[evidenceIndex] > 0 && (evidenceIndex < 2 || row.score >= minimum)
    ));
    eligibleMethods.sort((a, b) => b.score - a.score || a.index - b.index);
    const methodKeywords = unique(eligibleMethods.map((row) => row.label))
      .slice(0, Number(taxonomy.max_imaging_methods || 5));
    if (!methodKeywords.length) {
      methodKeywords.push(String(taxonomy.unidentified_imaging_method || '成像方法/待识别'));
    }

    return {
      categories: [],
      keywords: methodKeywords,
      confidence: eligibleMethods.length ? Math.min(1, eligibleMethods[0].score / 6) : 0,
      managementPath: null,
    };
  }

  function collectionPaths(taxonomy, result) {
    if (taxonomy && Array.isArray(taxonomy.profiles)) {
      const rootName = taxonomy.root || '微信文献';
      return [
        [rootName],
        [rootName, result.collectionDimension || '分类', result.primaryCategory || '待识别'],
      ];
    }
    const rootName = taxonomy.root || '微信文献';
    const pending = String(taxonomy.unidentified_imaging_method || '成像方法/待识别');
    const methods = unique(Array.isArray(result.keywords) && result.keywords.length
      ? result.keywords
      : [pending]);
    const paths = [[rootName]];
    for (const method of methods) {
      const leaf = method === pending ? '待识别' : method;
      paths.push([rootName, '成像方法', leaf]);
    }
    return paths;
  }

  function validateImportRequest(data) {
    const errors = [];
    if (!data || typeof data.path !== 'string' || !data.path.trim()) {
      errors.push('path is required');
    } else if (!data.path.toLocaleLowerCase('en').endsWith('.pdf')) {
      errors.push('path must end in .pdf');
    }
    if (!data || typeof data.sha256 !== 'string' || !data.sha256.trim()) {
      errors.push('sha256 is required');
    } else if (!/^[a-f0-9]{64}$/i.test(data.sha256)) {
      errors.push('sha256 must be a 64-character hex digest');
    }
    if (data && data.extraTags !== undefined) {
      if (!Array.isArray(data.extraTags)) {
        errors.push('extraTags must be an array');
      } else if (data.extraTags.length > 8) {
        errors.push('extraTags must contain at most 8 tags');
      } else if (data.extraTags.some((value) => (
        typeof value !== 'string' || !value.trim() || value.length > 200
      ))) {
        errors.push('extraTags contains an invalid tag');
      }
    }
    return errors;
  }

  function normalizeExtraTags(values) {
    if (!Array.isArray(values)) return [];
    return unique(values.map((value) => String(value || '')
      .replace(/[\u0000-\u001F]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 80))
      .filter(Boolean));
  }

  function sanitizeFilenamePart(value) {
    return String(value || '')
      .replace(/\.pdf\s*$/i, '')
      .replace(/[\u0000-\u001F<>:"/\\|?*]+/g, ' ')
      .replace(/\s+/g, ' ')
      .replace(/^[.\s]+|[.\s]+$/g, '');
  }

  function originalFilenameStem(value) {
    const leaf = String(value || '').split(/[\\/]/).pop() || '';
    return sanitizeFilenamePart(leaf);
  }

  function buildAttachmentFilename(input = {}) {
    const yearMatch = String(input.year || '').match(/(?:18|19|20|21)\d{2}/);
    const year = yearMatch ? yearMatch[0] : '未知年份';
    const title = sanitizeFilenamePart(input.title)
      || originalFilenameStem(input.originalFilename)
      || '未命名文献';
    const maxLength = Math.max(32, Number(input.maxLength) || 180);
    const prefix = `${year}-`;
    const suffix = '.pdf';
    const titleLimit = Math.max(1, maxLength - prefix.length - suffix.length);
    const boundedTitle = title.slice(0, titleLimit).replace(/[.\s]+$/g, '') || '未命名文献';
    return `${prefix}${boundedTitle}${suffix}`;
  }

  function buildSourceTag(value) {
    const prefix = '来源/微信/';
    const fallback = '未设置发送方';
    const sender = String(value || '')
      .replace(/[\u0000-\u001F/\\]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim() || fallback;
    return prefix + sender.slice(0, 80 - prefix.length).trim();
  }

  return {
    buildAttachmentFilename,
    buildSourceTag,
    classify,
    classifyProfile,
    collectionPaths,
    getProfile,
    listProfiles,
    normalizeExtraTags,
    normalizeText,
    unique,
    validateImportRequest,
  };
});

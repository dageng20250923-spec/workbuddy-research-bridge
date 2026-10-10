"""Explicit allowlist preparation archives; no third-party runtime or private state."""
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SKILL_FILES = (
    'paperflow-workbuddy/SKILL.md',
    'paperflow-workbuddy/references/workflow.md',
    'paperflow-workbuddy/references/local-client.md',
    'paperflow-workbuddy/scripts/report_jobs.py',
)


def validate_skill(folder):
    """Strict validation for this two-field frontmatter, without installing PyYAML."""
    text = (folder / 'SKILL.md').read_text(encoding='utf-8')
    front = re.match(r'\A---\nname: ([a-z0-9-]{1,64})\ndescription: ([^\n]+)\n---\n', text)
    if not front or front.group(1) != folder.name:
        raise ValueError('skill_frontmatter_invalid')
    if len(front.group(2)) > 1024 or any(c in front.group(2) for c in '<>'):
        raise ValueError('skill_description_invalid')
    # The authored YAML is deliberately plain text, not arbitrary YAML syntax.
    if any(c in front.group(2) for c in ':{}[]#'):
        raise ValueError('unsupported_frontmatter_syntax')
    for link in re.findall(r'\]\(([^)]+)\)', text):
        target = (folder / link).resolve()
        if not target.is_relative_to(folder.resolve()) or not target.is_file():
            raise ValueError('skill_reference_missing')
    if not (folder / 'scripts/report_jobs.py').is_file():
        raise ValueError('skill_script_missing')


def inputs():
    members = {name: HERE / name for name in SKILL_FILES}
    members['使用手册.md'] = HERE / '使用手册.md'
    members['验证记录.md'] = HERE / '验证记录.md'
    members['统一服务开发路径.md'] = ROOT / 'docs/plans/2026-10-09-shared-workflow.md'
    for archive_name, path in members.items():
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT) or not path.is_file():
            raise ValueError('unsafe_or_missing_package_input')
        if Path(archive_name).suffix not in ('.md', '.py'):
            raise ValueError('unexpected_package_member')
    return members


def write_archive(path, members):
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, source in members.items():
            archive.write(source, name)
        manifest = {'status':'preparation_not_public_release', 'contains_credentials':False,
                    'third_party_runtime_bundled':False,
                    'files': {name:hashlib.sha256(source.read_bytes()).hexdigest()
                              for name,source in members.items()}}
        archive.writestr('MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError('archive_integrity_failed')
    return {'path':str(path), 'bytes':path.stat().st_size,
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'members': sorted(list(members) + ['MANIFEST.json'])}


def main():
    if os.name == 'nt' and HERE.drive.upper() != 'D:':
        raise ValueError('D_drive_required')
    validate_skill(HERE / 'paperflow-workbuddy')
    members = inputs()
    dist = HERE / 'dist'
    if dist.is_symlink() or not dist.resolve().is_relative_to(ROOT):
        raise ValueError('unsafe_output_directory')
    skill_zip = dist / 'paperflow-workbuddy-0.1.0-preparation.zip'
    full_zip = dist / 'PaperFlow-0.1.0-migration-preparation.zip'
    if skill_zip.exists() or full_zip.exists():
        raise FileExistsError('preparation_artifact_exists_no_overwrite')
    dist.mkdir(exist_ok=True)
    results = [write_archive(skill_zip, {name:members[name] for name in SKILL_FILES}),
               write_archive(full_zip, members)]
    print(json.dumps({'validation':'strict_frontmatter_references_and_zip_integrity',
                      'artifacts':results}, ensure_ascii=False))


if __name__ == '__main__':
    main()

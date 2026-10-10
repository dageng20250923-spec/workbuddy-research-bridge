"""Build the explicit-source local phase package, excluding runtime and personal data."""
import os
from pathlib import Path
import sys

CLIENT = Path(__file__).resolve().parent
ROOT = CLIENT.parent
sys.path.insert(0,str(ROOT / 'workbuddy'))
from build_preparation import validate_skill, write_archive

CLIENT_FILES = ('app.py','core.py','cli.py','Start-PaperFlow.ps1','README.md','验证记录.md')
SKILL_FILES = ('SKILL.md','references/workflow.md','references/local-client.md','scripts/report_jobs.py')


def main():
    if os.name == 'nt' and CLIENT.drive.upper() != 'D:':
        raise ValueError('D_drive_required')
    skill = ROOT / 'workbuddy/paperflow-workbuddy'
    validate_skill(skill)
    local = {'client/'+name:CLIENT/name for name in CLIENT_FILES}
    skill_members = {'paperflow-workbuddy/'+name:skill/name for name in SKILL_FILES}
    members = dict(local,**skill_members)
    for path in members.values():
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT) or not path.is_file():
            raise ValueError('unsafe_or_missing_input')
    dist = CLIENT / 'dist'
    if dist.is_symlink() or not dist.resolve().is_relative_to(ROOT):
        raise ValueError('unsafe_output')
    full = dist / 'PaperFlow-0.2.0-local-client.zip'
    skill_zip = dist / 'paperflow-workbuddy-0.2.0-local-client.zip'
    if full.exists() or skill_zip.exists():
        raise FileExistsError('artifacts_exist_no_overwrite')
    dist.mkdir(exist_ok=True)
    import json
    print(json.dumps({'phase':'local_client_simulation_only', 'artifacts':[
        write_archive(full,members),write_archive(skill_zip,skill_members)]}, ensure_ascii=False))


if __name__ == '__main__':
    main()

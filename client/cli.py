"""Finite local CLI for WorkBuddy. Live execution is deliberately unavailable."""
import argparse
import json
from pathlib import Path
import sys

from core import Workspace, ClientError, check_paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('operation', choices=('init','inspect','simulate','report','export-workbuddy'))
    args = parser.parse_args(argv)
    try:
        workspace = Workspace(args.workspace)
        if args.operation == 'init':
            workspace.initialize()
            result = {'mode':'simulation','initialized':True, 'background_enabled':False}
        elif args.operation == 'inspect':
            result = check_paths(workspace.config())
        elif args.operation == 'simulate':
            result = workspace.simulate()
        elif args.operation == 'report':
            result = workspace.report()
        else:
            result = workspace.export_workbuddy(Path(__file__).resolve().parent)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except ClientError as error:
        print(json.dumps({'mode':'simulation','error':str(error),'live_started':False}, ensure_ascii=False))
        return 2
    except OSError:
        print(json.dumps({'mode':'simulation','error':'文件不可访问，未启动真实整理。','live_started':False}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())

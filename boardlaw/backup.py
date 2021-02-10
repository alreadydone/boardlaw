import sys
import time
import numpy as np
from pathlib import Path
import json
import b2sdk.v1 as b2
import aljpy
import multiprocessing
from pavlov import runs, files, storage

@aljpy.memcache()
def api(bucket):
    # Keys are in 1Password
    api = b2.B2Api()
    keys = json.loads(Path('credentials.json').read_text())['backblaze'][bucket]
    api.authorize_account('production', **keys)
    return api

def sync_up(local, remote):
    bucket, path = remote.split(':')
    workers = multiprocessing.cpu_count()
    syncer = b2.Synchronizer(workers)
    with b2.SyncReport(sys.stdout, False) as reporter:
        syncer.sync_folders(
            source_folder=b2.parse_sync_folder(local, api(bucket)),
            dest_folder=b2.parse_sync_folder(f'b2://{bucket}/{path}', api(bucket)),
            now_millis=int(round(time.time() * 1000)),
            reporter=reporter)

def sync_down(local, remote, **kwargs):
    bucket, path = remote.split(':')
    workers = multiprocessing.cpu_count()
    syncer = b2.Synchronizer(workers, **kwargs)
    with b2.SyncReport(sys.stdout, False) as reporter:
        syncer.sync_folders(
            source_folder=b2.parse_sync_folder(f'b2://{bucket}/{path}', api(bucket)),
            dest_folder=b2.parse_sync_folder(local, api(bucket)),
            now_millis=int(round(time.time() * 1000)),
            reporter=reporter)

def upload(local, remote):
    bucket, path = remote.split(':')
    bucket = api(bucket).get_bucket_by_name(remote)
    bucket.upload_local_file(
        local_file=local,
        file_name=path)

def cleanup():
    from tqdm.auto import tqdm
    from pavlov import files, runs, storage
    for run, _ in tqdm(runs.runs().items()):
        for _, fi in storage.snapshots(run).items():
            files.remove(run, fi['path'].name)


def ablate_run_snapshots(run):
    for i, info in storage.snapshots(run).items():
        if (i == 0) or (np.log2(i) % 1 == 0):
            pass
        else:
            print(run, info['path'].name)
            files.remove(run, info['path'].name)

def ablate_snapshots():
    for run, info in runs.runs().items():
        ablate_run_snapshots(run)


def backup():
    cleanup()
    sync_up('./output/pavlov', 'boardlaw:output/pavlov')
    sync_up('./output/experiments/architecture/results', 'boardlaw:output/experiments/architecture/results')

def fetch():
    sync_down('./output/pavlov', 'boardlaw:output/pavlov')

if __name__ == '__main__':
    backup()
from . import files
from logging import getLogger
from tempfile import NamedTemporaryFile
from subprocess import check_output, STDOUT


log = getLogger(__name__)

def archive(run=-1):
    with NamedTemporaryFile() as f:
        # Ignores .gitignore automagically, and doesn't depend on a git repo existing
        # so that we can use it on remote machines we've rsync'd to. Hooray!
        check_output('ag -0 -l . | xargs -0 tar -czvf ' + f.name, shell=True, stderr=STDOUT)
        contents = f.read()

    path = files.new_file(run, 'archive.tar.gz')
    path.write_bytes(contents)

def update():
    from pavlov import runs
    import git

    rows = runs.pandas().query('tag.notnull()')
    repo = git.Repo('.')
    for run, row in rows.iterrows():
        print(run)
        if not files.path(run, 'archive.tar.gz').exists():
            repo.git.checkout(f'tags/pavlov_{row.tag}')
            archive(run)
            
    for run, row in rows.iterrows():
        with runs.update(run) as i:
            del i['tag']
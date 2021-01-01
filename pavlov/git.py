from . import runs
from git import Repo

def tag_working_dir(tagname):
    #TODO: This doesn't handle deleted files properly right now; when I check the 
    # original branch back out, they get restored.

    # Stage everything
    # Create a new branch
    # Make a commit
    # Tag it
    # Checkout master
    # Checkout contents of new branch
    # Delete new branch
    r = Repo()

    branch = r.active_branch.name

    r.git.add('*')
    r.git.checkout('-b', '_pavlov')
    r.git.commit('-a', '-m', '"pavlov checkpoint"', '--allow-empty')
    r.git.tag(f'pavlov_{tagname}')
    r.git.checkout(branch)
    r.git.checkout('_pavlov', '--', '.')
    r.git.branch('-D', '_pavlov')

def tag(run):
    run = runs.resolve(run)
    #TODO: Sanitise this properly
    tagname = run.replace(' ', '_')
    tag_working_dir(tagname)
    with runs.update(run) as i:
        i['tag'] = tagname

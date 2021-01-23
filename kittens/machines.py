import os
import psutil
from subprocess import Popen
TYPES = {}

#TODO: Is there a way to not create zombies in the first place?
DEAD = ('zombie',)

def register(cls):
    TYPES[cls.__name__] = cls
    return cls

def resource_env(j, m):
    env = os.environ.copy()
    for k in j['resources']:
        end = str(m['resources'][k])
        start = m['resources'][k] - j['resources'][k]
        env[f'KITTENS_{k.upper()}'] = f'{start}:{end}'
    return env

@register
class Local:

    @staticmethod
    def machines():
        return [{
            'name': 'local',
            'resources': {'gpu': 2, 'memory': 64},
            'processes': [p.info['pid'] for p in psutil.process_iter(['pid', 'status']) if p.info['status'] not in DEAD]}]

    @staticmethod
    def launch(j, m):
        p = Popen(j['command'], 
            start_new_session=True, 
            shell=True,
            env=resource_env(j, m))
        return p.pid

def machines():
    ms = {}
    for t, cls in TYPES.items():
        for m in cls.machines():
            m['type'] = t
            ms[m['name']] = m
    return ms
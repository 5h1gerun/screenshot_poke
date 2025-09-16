import ast
targets = [
    'combined_app.py',
    'app/__init__.py',
    'app/obs_client.py',
    'app/utils/image.py',
    'app/utils/logging.py',
    'app/threads/double_battle.py',
    'app/threads/rkaisi_teisi.py',
    'app/threads/syouhai.py',
    'app/ui/app.py',
]
for p in targets:
    try:
        ast.parse(open(p,'r',encoding='utf-8').read(), filename=p)
        print(p, 'OK')
    except Exception as e:
        print(p, 'ERROR:', e)

import ast
for p in ['combined_app.py','textual_app.py','desktop_wrapper.py']:
    try:
        ast.parse(open(p,'r',encoding='utf-8').read(), filename=p)
        print(p, 'OK')
    except Exception as e:
        print(p, 'ERROR:', e)

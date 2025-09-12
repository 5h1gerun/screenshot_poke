import textual
import textual.widgets as w
print('textual version:', getattr(textual, '__version__', '?'))
print('Has TextLog:', hasattr(w, 'TextLog'))
print('Has Log:', hasattr(w, 'Log'))
Log = getattr(w, 'Log', None)
if Log:
    methods = [m for m in dir(Log) if m in ('write','write_line','log','write_text','append','add_line')]
    print('Log methods:', methods)

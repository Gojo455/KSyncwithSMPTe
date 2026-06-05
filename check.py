with open('app.py', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()
for i, l in enumerate(lines[1160:1205], start=1161):
    print(f'{i}: {l}', end='')
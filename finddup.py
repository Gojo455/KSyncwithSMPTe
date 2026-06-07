with open('app.py', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

# Delete lines 1316 to 1353 (the second duplicate, 1-indexed)
cleaned = lines[:1315] + lines[1353:]

with open('app.py', 'w', encoding='utf-8') as f:
    f.writelines(cleaned)

print('Done — deleted lines 1316-1353')
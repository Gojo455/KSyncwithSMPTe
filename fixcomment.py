with open('app.py', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()
for i, l in enumerate(lines, 1):
    if 'admin_showtime_detail' in l or 'admin/showtimes/<' in l:
        print(f'{i}: {l}', end='')
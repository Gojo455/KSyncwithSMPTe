with open('app.py', encoding='utf-8', errors='ignore') as f:
    content = f.read()

content = content.replace(
    '        # Bookings by cinema → data.zones\n        # Bookings by cinema → data.zones',
    '        # Bookings by cinema → data.zones'
)

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done')
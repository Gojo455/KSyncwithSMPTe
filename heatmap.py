import psycopg2
import psycopg2.extras
import json
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import seaborn as sns
import os
import argparse
import tkinter as tk
from tkinter import ttk, messagebox

DATABASE_URL = os.environ.get('DATABASE_URL')

def generate_heatmap(showtime_id=1, hall_label="FilmHouse Silver"):
    """
    Pulls all seats for a given showtime and draws a
    colour-coded quality heatmap matching cinema layout.
    """
    db  = psycopg2.connect(DATABASE_URL,
              cursor_factory=psycopg2.extras.RealDictCursor)
    cur = db.cursor()
    cur.execute("""
        SELECT row_num, col_num, row_label,
               seat_number, quality_score, status
        FROM seats
        WHERE showtime_id = %s
        ORDER BY row_num, col_num
    """, (showtime_id,))
    seats = cur.fetchall()
    cur.close(); db.close()

    if not seats:
        print(f"No seats found for showtime {showtime_id}")
        return

    max_row = max(s['row_num'] for s in seats)
    max_col = max(s['col_num'] for s in seats)

    # Build 2D grid of quality scores
    grid        = np.zeros((max_row, max_col))
    label_grid  = [[''] * max_col for _ in range(max_row)]

    for s in seats:
        r = s['row_num'] - 1
        c = s['col_num'] - 1
        grid[r][c]       = float(s['quality_score'])
        label_grid[r][c] = f"{s['row_label']}{s['seat_number']}\n{s['quality_score']}"

    # Row labels (A, B, C...)
    row_labels = []
    for r in range(1, max_row + 1):
        matching = [s for s in seats if s['row_num'] == r]
        row_labels.append(matching[0]['row_label'] if matching else str(r))

    # ── Draw the heatmap ─────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(max_col * 0.85, max_row * 0.85))

    cmap = sns.color_palette("RdYlGn", as_cmap=True)

    sns.heatmap(
        grid,
        ax          = ax,
        cmap        = cmap,
        vmin        = 0,
        vmax        = 10,
        linewidths  = 0.8,
        linecolor   = '#cccccc',
        annot       = np.array(label_grid),
        fmt         = '',
        annot_kws   = {'size': 7},
        cbar_kws    = {'label': 'Q_obj Score (0–10)'},
        xticklabels = [str(i) for i in range(1, max_col + 1)],
        yticklabels = row_labels,
    )

    # Screen label at top
    ax.set_title(
        f'Seat Quality Heatmap — {hall_label}\n'
        f'Based on SMPTE EG 18-1994 Cinema Viewing Standards',
        fontsize=13, fontweight='bold', pad=16
    )
    ax.set_xlabel('Seat Number (Column)',  fontsize=10)
    ax.set_ylabel('Row',                   fontsize=10)

    # Add SCREEN label above the plot
    fig.text(0.5, 0.98, '▬▬▬▬▬  SCREEN  ▬▬▬▬▬',
             ha='center', va='top', fontsize=11,
             color='steelblue', fontweight='bold')

    plt.tight_layout(rect=[0, 0, 1, 0.96])

    output_path = 'seat_heatmap.png'
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.show()
    print(f"\nHeatmap saved to {output_path}")
    print("\nExpected pattern (SMPTE validation):")
    print(f"  Highest score in grid : {grid.max():.2f}  (should be center-back rows)")
    print(f"  Lowest score in grid  : {grid.min():.2f}  (should be front corners)")
    print(f"  Row with best avg     : {row_labels[np.argmax(grid.mean(axis=1))]}")
    print(f"  Col with best avg     : {np.argmax(grid.mean(axis=0)) + 1}")


def list_showtimes_for_cinema(cinema_name):
    """Return showtimes matching `cinema_name` (partial, case-insensitive)."""
    db  = psycopg2.connect(DATABASE_URL,
              cursor_factory=psycopg2.extras.RealDictCursor)
    cur = db.cursor()
    cur.execute("""
        SELECT id, cinema_name, hall_name, showtime
        FROM showtimes
        WHERE cinema_name ILIKE %s
        ORDER BY showtime
    """, (f"%{cinema_name}%",))
    rows = cur.fetchall()
    cur.close(); db.close()
    return [dict(r) for r in rows]


def _print_showtimes(rows):
    if not rows:
        print('No showtimes found for that cinema.')
        return
    print(f"Found {len(rows)} showtime(s):")
    for r in rows:
        print(f"  id={r['id']:>4}  |  {r['showtime']}  |  {r['cinema_name']} — {r['hall_name']}")


def gui_select_hall_and_showtime():
    """Open a simple Tkinter dialog to pick a cinema/hall and showtime, then generate the heatmap."""
    try:
        root = tk.Tk()
    except Exception as e:
        print('Unable to start GUI:', e)
        return

    root.title('Select Hall / Showtime')

    # Fetch distinct cinema+hall combos
    db  = psycopg2.connect(DATABASE_URL,
              cursor_factory=psycopg2.extras.RealDictCursor)
    cur = db.cursor()
    cur.execute("""
        SELECT DISTINCT cinema_name, hall_name
        FROM showtimes
        ORDER BY cinema_name, hall_name
    """)
    combos = cur.fetchall()
    cur.close(); db.close()

    options = [f"{r['cinema_name']} — {r['hall_name']}" for r in combos]

    frame = ttk.Frame(root, padding=12)
    frame.grid(row=0, column=0, sticky='nsew')

    ttk.Label(frame, text='Choose cinema + hall:').grid(row=0, column=0, sticky='w')
    combo_var = tk.StringVar()
    combo = ttk.Combobox(frame, textvariable=combo_var, values=options, width=60)
    combo.grid(row=1, column=0, pady=6)
    if options:
        combo.current(0)

    showtimes_listbox = tk.Listbox(frame, width=80, height=10)
    showtimes_listbox.grid(row=3, column=0, pady=8)

    def load_showtimes():
        sel = combo_var.get()
        if not sel:
            messagebox.showinfo('Select', 'Please choose a cinema + hall from the dropdown.')
            return
        cinema, hall = [s.strip() for s in sel.split('—')]
        cinema = cinema.strip(); hall = hall.strip()

        db2  = psycopg2.connect(DATABASE_URL,
                  cursor_factory=psycopg2.extras.RealDictCursor)
        cur2 = db2.cursor()
        cur2.execute("""
            SELECT id, showtime FROM showtimes
            WHERE cinema_name = %s AND hall_name = %s
            ORDER BY showtime
        """, (cinema, hall))
        rows = cur2.fetchall()
        cur2.close(); db2.close()

        showtimes_listbox.delete(0, tk.END)
        for r in rows:
            showtimes_listbox.insert(tk.END, f"{r['id']:>4}  |  {r['showtime']}")
        if not rows:
            showtimes_listbox.insert(tk.END, 'No showtimes found for this hall.')

    def generate_from_selection(event=None):
        sel_combo = combo_var.get()
        if not sel_combo:
            messagebox.showinfo('Select', 'Please choose a cinema + hall first.')
            return
        sel_index = showtimes_listbox.curselection()
        if not sel_index:
            messagebox.showinfo('Select', 'Please select a showtime from the list.')
            return
        entry = showtimes_listbox.get(sel_index[0])
        # entry format: '  id  |  showtime'
        try:
            sid = int(entry.split('|')[0].strip())
        except Exception:
            messagebox.showerror('Error', 'Invalid showtime selection.')
            return
        # Build a friendly label
        cinema, hall = [s.strip() for s in sel_combo.split('—')]
        label = f"{cinema} — {hall}"
        root.destroy()
        generate_heatmap(showtime_id=sid, hall_label=label)

    btn_frame = ttk.Frame(frame)
    btn_frame.grid(row=2, column=0, pady=6, sticky='w')
    ttk.Button(btn_frame, text='Load Showtimes', command=load_showtimes).grid(row=0, column=0, padx=4)
    ttk.Button(btn_frame, text='Generate Heatmap', command=generate_from_selection).grid(row=0, column=1, padx=4)

    showtimes_listbox.bind('<Double-Button-1>', generate_from_selection)

    root.mainloop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate seat-quality heatmap or list showtimes for a cinema')
    parser.add_argument('--list', '-l', dest='list_cinema', help='List showtimes for a cinema name (partial match)')
    parser.add_argument('--showtime', '-s', dest='showtime_id', type=int, help='Showtime ID to generate heatmap for')
    parser.add_argument('--label', '-t', dest='hall_label', default=None, help='Optional hall label to use in plot title')

    args = parser.parse_args()

    if args.list_cinema:
        rows = list_showtimes_for_cinema(args.list_cinema)
        _print_showtimes(rows)
    elif args.showtime_id:
        label = args.hall_label if args.hall_label is not None else f"Showtime {args.showtime_id}"
        generate_heatmap(showtime_id=args.showtime_id, hall_label=label)
    else:
        # No CLI args -> open GUI by default (Option B)
        try:
            gui_select_hall_and_showtime()
        except Exception as e:
            print('GUI failed to start, falling back to default showtime heatmap:', e)
            generate_heatmap(showtime_id=1, hall_label="FilmHouse Silver (8×12)")
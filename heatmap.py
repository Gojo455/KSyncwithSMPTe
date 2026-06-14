import psycopg2
import psycopg2.extras
import json
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import seaborn as sns
import os

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


if __name__ == '__main__':
    # showtime_id=1 is always the first seeded showtime
    # Change it to any showtime_id in your database
    generate_heatmap(showtime_id=1, hall_label="FilmHouse Silver (8×12)")
# Run these in a separate test file or PyCharm console
from app import compute_seat_quality

# Centre seat should score highest
print(compute_seat_quality(6, 8, 10, 16))   # expect close to 10.0

# Front row should score low due to penalty
print(compute_seat_quality(1, 8, 10, 16))   # expect below 4.0

# Corner seat should score lowest
print(compute_seat_quality(1, 1, 10, 16))   # expect close to 0.5

# Back row should have moderate penalty
print(compute_seat_quality(9, 8, 10, 16))  # expect around 5.0–6.0

# def compute_seat_quality(row, col, total_rows, total_cols)
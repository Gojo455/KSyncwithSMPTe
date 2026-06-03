from app import jaccard

# Identical sets should return 1.0
print(jaccard({'Action', 'Drama'}, {'Action', 'Drama'}))  # 1.0

# No overlap should return 0.0
print(jaccard({'Action'}, {'Drama'}))  # 0.0

# Partial overlap
print(jaccard({'Action', 'Drama'}, {'Action', 'Horror'}))  # 0.333
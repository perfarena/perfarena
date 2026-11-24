# Pre-processing
counts = [[0] * n for _ in range(m)]
for j in range(n):
    for i in range(m - 1, -1, -1):
        if matrix[i][j] == 1:
            counts[i][j] = counts[i + 1][j] + 1 if i < m - 1 else 1
# Step 1: Transpose the Matrix
for i in range(len(matrix)):
    for j in range(i, len(matrix[0])):
        matrix[i][j], matrix[j][i] = matrix[j][i], matrix[i][j]

# Step 2: Reverse Each Row
for row in matrix:
    left, right = 0, len(row) - 1
    while left < right:
        row[left], row[right] = row[right], row[left]
        left += 1
        right -= 1
# Get the length of the matrix
n = len(matrix)

# Transpose the matrix
for row in range(n):
    for column in range(row, n):
        matrix[row][column], matrix[column][row] = matrix[column][row], matrix[row][column]

# Reverse each row of the transposed matrix
for row in matrix:
    row.reverse()
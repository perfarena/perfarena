# Declare zero position array
zero_positions = []

# Loop through the matrix and find all the zeros
for i in range(len(matrix)):
    for j in range(len(matrix[0])):
        if matrix[i][j] == 0:
            zero_positions.append((i, j))

# Loop through zero positions
for pos in zero_positions:
    row, col = pos
    
    # Update the row to zeros
    for i in range(len(matrix[0])):
        matrix[row][i] = 0
    
    # Update the column to zeros
    for i in range(len(matrix)):
        matrix[i][col] = 0
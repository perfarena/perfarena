# Initialize flags
firstRowZero = False
firstColZero = False

# Mark indicators and update first row and first column
for i in range(len(matrix)):
    for j in range(len(matrix[0])):
        if matrix[i][j] == 0:
            if i == 0:
                firstRowZero = True
            if j == 0:
                firstColZero = True
            matrix[i][0] = 0
            matrix[0][j] = 0

# Update the rest of the matrix based on indicators
for i in range(1, len(matrix)):
    for j in range(1, len(matrix[0])):
        if matrix[i][0] == 0 or matrix[0][j] == 0:
            matrix[i][j] = 0

# Zero out the first row and first column if needed
if firstRowZero:
    for j in range(len(matrix[0])):
        matrix[0][j] = 0
if firstColZero:
    for i in range(len(matrix)):
        matrix[i][0] = 0
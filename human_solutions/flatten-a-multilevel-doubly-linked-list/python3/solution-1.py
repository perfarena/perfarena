# Iterate through the matrix indices
for i in range(matrix.length - 1):
    for j in range(matrix[0].length - 1):
        # Check if the current element is not equal to the one below and to the right
        if matrix[i][j] != matrix[i + 1][j + 1]:
            return False

# If all comparisons pass, return True
return True
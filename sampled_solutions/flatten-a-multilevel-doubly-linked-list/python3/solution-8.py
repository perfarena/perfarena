def is_topless(matrix):
    for i in range(1, len(matrix)):
        for j in range(1, len(matrix[0])):
            if matrix[i][j] != matrix[i-1][j-1]:
                return False
    return True

# Test the function
matrix = [[2, 3, 5], [1, 2, 3], [5, 1, 2]]
print(is_topless(matrix))  # Output: True
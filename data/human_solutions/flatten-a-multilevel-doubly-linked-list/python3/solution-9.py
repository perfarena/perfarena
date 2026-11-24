def is_topless(matrix):
    # Check diagonals from top-left to bottom-right
    for i in range(len(matrix)):
        if matrix[i][i] != matrix[0][0]:
            return False
    
    # Check diagonals from top-right to bottom-left
    for i in range(1, len(matrix)):
        if matrix[i][len(matrix) - i - 1] != matrix[0][0]:
            return False
    
    return True

# Test the function
matrix = [
    [3, 2, 1, 5],
    [4, 3, 1, 5],
    [4, 2, 3, 5],
    [4, 2, 1, 3]
]

result = is_topless(matrix)
print(result)  # Output: False
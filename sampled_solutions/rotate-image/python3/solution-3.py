def rotate_image(matrix):
    n = len(matrix)
    # Swap rows with columns
    for i in range(n):
        for j in range(i, n):
            matrix[i][j], matrix[j][i] = matrix[j][i], matrix[i][j]
    
    # Swap columns
    for i in range(n):
        for j in range(n // 2):
            matrix[i][j], matrix[i][n - 1 - j] = matrix[i][n - 1 - j], matrix[i][j]

# Example usage
matrix = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
rotate_image(matrix)
for row in matrix:
    print(row)
def rotate(matrix):
    for row in range(len(matrix)):
        for column in range(row, len(matrix[0])):
            matrix[row][column], matrix[column][row] = matrix[column][row], matrix[row][column]
    
    for row in matrix:
        row.reverse()

# Example Usage
matrix = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
rotate(matrix)
print(matrix)
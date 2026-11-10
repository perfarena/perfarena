# Initialize an empty array to store zero element addresses
zeros_addresses = []

# Iterate over the matrix to find zero elements
for row in range(m):
    for column in range(n):
        if matrix[row][column] == 0:
            zeros_addresses.append((row, column))

# Helper function to set rows and columns to zero based on zero element addresses
def set_zeros(row, column, matrix):
    for i in range(len(matrix)):
        matrix[i][column] = 0  # Set entire column to zero
    for j in range(len(matrix[0]):
        matrix[row][j] = 0  # Set entire row to zero

# Iterate over zero element addresses and set corresponding rows and columns to zero
for row, column in zeros_addresses:
    set_zeros(row, column, matrix)
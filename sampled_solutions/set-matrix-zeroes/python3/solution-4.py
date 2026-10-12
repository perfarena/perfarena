def setZeroes(matrix):
    first_row_has_zero = False
    
    # Check if the first row contains zero
    for col in range(len(matrix[0])):
        if matrix[0][col] == 0:
            first_row_has_zero = True
            break
    
    # Mark zeros in the rest of the matrix
    for row in range(1, len(matrix)):
        for col in range(len(matrix[0])):
            if matrix[row][col] == 0:
                matrix[0][col] = 0
                matrix[row][0] = 0
    
    # Set zeros based on the first row and column
    for row in range(1, len(matrix)):
        for col in range(1, len(matrix[0])):
            if matrix[0][col] == 0 or matrix[row][0] == 0:
                matrix[row][col] = 0
    
    # Handle the first row separately
    if first_row_has_zero:
        for col in range(len(matrix[0])):
            matrix[0][col] = 0
    
    return matrix
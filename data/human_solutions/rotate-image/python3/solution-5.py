def rotate_image(matrix):
    # Step 1: Reverse the Matrix
    matrix.reverse()
    
    # Step 2: Swap Numbers along the Main Diagonal
    for r in range(len(matrix)):
        for c in range(r):
            matrix[r][c], matrix[c][r] = matrix[c][r], matrix[r][c]
    
    # Step 3: Return the Rotated Matrix
    return matrix
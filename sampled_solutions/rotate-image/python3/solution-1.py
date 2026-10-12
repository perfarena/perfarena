# Function to rotate the image by 90 degrees clockwise
def rotate_image(matrix):
    n = len(matrix)
    
    # Transpose the matrix
    for i in range(n):
        for j in range(i, n):
            matrix[i][j], matrix[j][i] = matrix[j][i], matrix[i][j]
    
    # Reverse each row to get the rotated matrix
    for i in range(n):
        matrix[i] = matrix[i][::-1]
    
    return matrix

# Test the function with the given input matrix
input_matrix = [
    [1, 2, 3],
    [4, 5, 6],
    [7, 8, 9]
]

rotated_matrix = rotate_image(input_matrix)
print(rotated_matrix)
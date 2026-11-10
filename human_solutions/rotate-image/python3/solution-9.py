def transpose(matrix):
    rows = len(matrix)
    columns = len(matrix[0])
    
    for i in range(rows):
        for j in range(i + 1, columns):
            matrix[i][j], matrix[j][i] = matrix[j][i], matrix[i][j]
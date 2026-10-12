def reverse_rows(matrix):
    rows = len(matrix)
    columns = len(matrix[0])
    
    for i in range(rows):
        for j in range(columns // 2):
            matrix[i][j], matrix[i][columns - j - 1] = matrix[i][columns - j - 1], matrix[i][j]